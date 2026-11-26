"""LLM 配置、目标 BD 评分、同步与流式分析路由（统一收敛前置上下文组装逻辑）。"""
from __future__ import annotations

import json
import queue
import threading
from typing import Any, Callable, Optional

from backend.api.routes.builds import persist_ai_positioning_if_matching
from backend.domain import ablation_dossier, build_profile, gap_analyzer, llm_advisor
from backend.engine import pob_parser, runefix
from backend.market.price_store import pob_fingerprint


def _active_league(rt: Any, body: dict[str, Any]) -> str:
    return str(
        body.get("league")
        or (rt.store.get_target_build() or {}).get("league")
        or (rt.store.get_prices() or {}).get("league")
        or "Standard"
    )


def prepare_llm_target_context(
    rt: Any,
    body: dict[str, Any],
    mode: str,
    *,
    emit: Optional[Callable[[dict[str, Any]], None]] = None,
    require_profile: bool = True,
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """统一准备 /llm/rate、/llm/analyze 与 /llm/analyze/stream 所需的 (raw_code, profile, pob_sandbox)。"""
    raw_code = body.get("pobCode") or body.get("xml") or (rt.store.get_target_build() or {}).get("pobCode") or ""
    prof = body.get("profile") if isinstance(body.get("profile"), dict) else None
    price_snap = rt.store.get_prices()
    valid_prices = price_snap if price_snap and not price_snap.get("empty") else None

    if mode == "rate":
        has_v2_facts = (
            isinstance(prof, dict)
            and int(((prof.get("engineFacts") or {}).get("metricsVersion")) or 0) >= 5
            and isinstance((prof.get("engineFacts") or {}).get("skillMechanics"), dict)
            and isinstance((prof.get("engineFacts") or {}).get("mobility"), dict)
            and isinstance(prof.get("simulation"), dict)
        )
        if not has_v2_facts:
            if not raw_code:
                raise ValueError("缺少目标 BD PoB2 分享码")
            cached_sim = ablation_dossier.get_cached_base_sim(
                raw_code,
                state_dir=rt.store.state_dir,
                require_mobility_fields=True,
            )
            if cached_sim is None:
                xml_fresh = pob_parser.decode_pob(raw_code)
                norm_fresh, _ = runefix.normalize_runes(xml_fresh)
                cached_sim = rt.pool().simulate(norm_fresh, include_audit=True)
                ablation_dossier.update_cached_base_sim(raw_code, cached_sim, state_dir=rt.store.state_dir)
            prof = build_profile.extract_profile(
                raw_code,
                pool=None,
                run_simulation=True,
                run_ablation=False,
                cached_base_sim=cached_sim,
                price_snapshot=valid_prices,
                use_ai=False,
            )
    else:
        need_sim_refresh = not isinstance(prof, dict) or (
            mode == "target"
            and int(((prof.get("engineFacts") or {}).get("metricsVersion")) or 0) < 5
            and bool(body.get("runSimulation", True))
        )
        if need_sim_refresh:
            if raw_code:
                if emit:
                    emit({
                        "type": "tool_status",
                        "stage": "running",
                        "summary": "⚙️ 正在解析目标 BD 并同步 PoB2 引擎面板...",
                    })
                want_ablation = bool(body.get("runAblation", mode == "target"))
                cached_sim = (
                    ablation_dossier.get_cached_base_sim(
                        raw_code,
                        state_dir=rt.store.state_dir,
                        require_mobility_fields=(mode == "target"),
                    )
                    if not want_ablation
                    else None
                )
                need_pool = bool(body.get("runSimulation", True)) and (want_ablation or cached_sim is None)
                prof = build_profile.extract_profile(
                    raw_code,
                    pool=rt.pool() if need_pool else None,
                    run_simulation=bool(body.get("runSimulation", True)),
                    run_ablation=want_ablation,
                    cached_base_sim=cached_sim,
                    price_snapshot=price_snap,
                    use_ai=False,
                )
            elif require_profile and not isinstance(prof, dict):
                raise ValueError("缺少目标 BD PoB2 分享码")

    dossier = body.get("ablationDossier") if isinstance(body.get("ablationDossier"), dict) else None
    if dossier is None and raw_code:
        dossier = ablation_dossier.get_cached_dossier_by_build(raw_code, state_dir=rt.store.state_dir)
    if isinstance(dossier, dict) and isinstance(prof, dict):
        prof = {**prof, "ablationDossier": dossier}

    if (
        mode in ("target", "rate")
        and isinstance(prof, dict)
        and not (isinstance(dossier, dict) and isinstance(dossier.get("ascendancy"), dict))
        and raw_code
    ):
        try:
            if emit:
                emit({
                    "type": "tool_status",
                    "stage": "running",
                    "summary": "⚙️ 正在执行升华天赋消融实算...",
                })
            asc_ab = ablation_dossier.ablate_ascendancy_only(
                raw_code,
                rt.pool(),
                base_sim=prof.get("simulation") if isinstance(prof.get("simulation"), dict) else None,
            )
            if asc_ab:
                prof = {**prof, "ascendancyAblation": asc_ab}
        except Exception:
            pass

    pob_sandbox = None
    if mode == "copy" and isinstance(prof, dict) and raw_code:
        try:
            if emit:
                emit({
                    "type": "tool_status",
                    "stage": "running",
                    "summary": "⚙️ 正在准备 PoB2 倒推灵敏度与等价词缀池（命中缓存毫秒级）...",
                })
            xml_c = pob_parser.decode_pob(raw_code)
            norm_c, _ = runefix.normalize_runes(xml_c)
            base_sim_c = prof.get("simulation") if isinstance(prof.get("simulation"), dict) else None
            if base_sim_c is None:
                base_sim_c = ablation_dossier.get_cached_base_sim(
                    raw_code,
                    state_dir=rt.store.state_dir,
                    require_mobility_fields=False,
                )
            if base_sim_c is None:
                base_sim_c = rt.pool().simulate(norm_c, include_audit=False)
            rev_c = ablation_dossier.ensure_reverse_spec(
                raw_code,
                rt.pool(),
                dossier=dossier if isinstance(dossier, dict) else None,
                base_sim=base_sim_c,
                state_dir=rt.store.state_dir,
            )
            if isinstance(rev_c, dict):
                prof = {**prof, "reverseSpec": rev_c}
            pob_sandbox = {"norm_xml": norm_c, "base_sim": base_sim_c, "pool": rt.pool()}
        except Exception:
            pob_sandbox = None

    return raw_code, prof, pob_sandbox


def _save_stream_plan_if_needed(
    rt: Any,
    body: dict[str, Any],
    mode: str,
    raw_code: str,
    prof: Optional[dict[str, Any]],
    copy_ctx: Optional[dict[str, Any]],
    active_league: str,
    ev: dict[str, Any],
) -> dict[str, Any]:
    try:
        pob_code_for_hash = raw_code or (rt.store.get_target_build() or {}).get("pobCode") or ""
        pob_h = pob_fingerprint(pob_code_for_hash) if mode != "consult" else ""
        tgt_rec = rt.store.get_target_build() or {}
        b_title = str(
            body.get("buildTitle")
            or ((prof or {}).get("positioning") or {}).get("archetype")
            or tgt_rec.get("title")
            or ((copy_ctx or {}).get("freeformQuery") or {}).get("skillIdea")
            or (copy_ctx or {}).get("goalNote")
            or "未命名方案"
        ).strip()
        reuse_id = str(body.get("planId") or "").strip()
        if not reuse_id:
            existing_plans = rt.store.list_copy_plans(pob_hash=pob_h if pob_h else None, mode=mode)
            for ep in existing_plans:
                if str(ep.get("pobHash") or "") != pob_h:
                    continue
                if mode in ("target", "gap"):
                    reuse_id = str(ep.get("id") or "")
                    break
                ep_ctx = ep.get("copyContext") or {}
                cur_ctx = copy_ctx or {}
                if (
                    int(ep_ctx.get("characterLevel") or 90) == int(cur_ctx.get("characterLevel") or 90)
                    and float(ep_ctx.get("budgetDivine") or 15) == float(cur_ctx.get("budgetDivine") or 15)
                    and str(ep_ctx.get("goalNote") or "").strip() == str(cur_ctx.get("goalNote") or "").strip()
                    and str(ep.get("league") or "Standard").strip() == active_league
                ):
                    reuse_id = str(ep.get("id") or "")
                    break
        saved_rec = rt.store.save_copy_plan({
            "id": reuse_id or None,
            "mode": mode,
            "pobHash": pob_h,
            "buildTitle": b_title,
            "league": active_league,
            "model": str(ev.get("model") or ""),
            "copyContext": copy_ctx,
            "text": str(ev.get("text") or ""),
            "reasoning": str(ev.get("reasoning") or ""),
            "copyPlan": ev.get("copyPlan"),
        })
        return {**ev, "savedPlan": saved_rec}
    except Exception:
        return ev


def handle_get(rt: Any, path: str, _raw_url: str) -> Optional[tuple[int, dict[str, Any]]]:
    if path == "/llm/config":
        cfg = llm_advisor.load_llm_config(rt.store.state_dir)
        return 200, {"ok": True, "config": llm_advisor.public_llm_config(cfg)}
    return None


def handle_post(rt: Any, path: str, body: dict[str, Any]) -> Optional[tuple[int, dict[str, Any]]]:
    if path == "/llm/config":
        cfg = llm_advisor.save_llm_config(body, state_dir=rt.store.state_dir)
        test_result = None
        if body.get("test"):
            test_result = llm_advisor.analyze_with_llm(
                "test",
                cfg=cfg,
                state_dir=rt.store.state_dir,
            )
        return 200, {
            "ok": True,
            "config": llm_advisor.public_llm_config(cfg),
            "testResult": test_result,
        }

    if path == "/llm/rate":
        use_cache = not bool(body.get("forceRefresh", False))
        raw_code, prof, _ = prepare_llm_target_context(rt, body, "rate")
        rating_res = llm_advisor.rate_target_with_ai(
            prof,
            state_dir=rt.store.state_dir,
            use_cache=use_cache,
        )
        merged_pos = rating_res.get("positioning") or (prof.get("positioning") if isinstance(prof, dict) else None)
        if isinstance(merged_pos, dict) and raw_code:
            persist_ai_positioning_if_matching(rt, raw_code, merged_pos)
        return 200, {
            "ok": True,
            "rating": rating_res,
            "positioning": merged_pos,
            "simulation": prof.get("simulation") if isinstance(prof, dict) else None,
            "engineFacts": prof.get("engineFacts") if isinstance(prof, dict) else None,
        }

    if path == "/llm/analyze":
        mode = str(body.get("mode") or "target")
        copy_ctx = body.get("copyContext") if isinstance(body.get("copyContext"), dict) else None
        essence_sum = str(body.get("essenceSummary") or "")
        active_league = _active_league(rt, body)

        if mode in ("target", "copy", "rate"):
            raw_code, prof, pob_sandbox = prepare_llm_target_context(rt, body, mode)
            res = llm_advisor.analyze_with_llm(
                mode,
                profile=prof,
                copy_context=copy_ctx,
                price_snapshot=rt.store.get_prices(),
                essence_summary=essence_sum,
                league=active_league,
                state_dir=rt.store.state_dir,
                pob_sandbox=pob_sandbox,
            )
            merged_pos = (
                res.get("positioning")
                if mode == "rate"
                else (
                    llm_advisor.merge_ai_positioning(
                        prof["positioning"],
                        res["structured"],
                        model=str(res.get("model") or ""),
                        engine_metrics_summary=llm_advisor._format_engine_metrics_summary(prof),
                    )
                    if mode == "target"
                    and isinstance(res.get("structured"), dict)
                    and isinstance(prof, dict)
                    and isinstance(prof.get("positioning"), dict)
                    else (prof.get("positioning") if isinstance(prof, dict) else None)
                )
            )
            if mode in ("target", "rate") and isinstance(merged_pos, dict) and raw_code:
                persist_ai_positioning_if_matching(rt, raw_code, merged_pos)
            return 200, {"ok": True, "analysis": res, "positioning": merged_pos, "copyPlan": res.get("copyPlan")}

        if mode == "consult":
            res = llm_advisor.analyze_with_llm(
                "consult",
                copy_context=copy_ctx,
                league=active_league,
                state_dir=rt.store.state_dir,
            )
            return 200, {"ok": True, "analysis": res, "copyPlan": res.get("copyPlan")}

        if mode == "gap":
            gap_rep = body.get("gapReport")
            if not isinstance(gap_rep, dict):
                my_code = body.get("myPobCode") or (rt.store.get_my_build() or {}).get("pobCode") or ""
                tgt_code = body.get("targetPobCode") or (rt.store.get_target_build() or {}).get("pobCode") or ""
                if not my_code or not tgt_code:
                    raise ValueError("请先同时导入「我的 BD」与「目标 BD」")
                gap_rep = gap_analyzer.analyze_gap(
                    my_code,
                    tgt_code,
                    rt.pool(),
                    price_snapshot=rt.store.get_prices(),
                )
            res = llm_advisor.analyze_with_llm(
                "gap",
                gap_report=gap_rep,
                state_dir=rt.store.state_dir,
            )
            return 200, {"ok": True, "analysis": res}

        if mode == "test":
            res = llm_advisor.analyze_with_llm("test", state_dir=rt.store.state_dir)
            return 200, {"ok": True, "analysis": res}

        raise ValueError(f"不支持的分析模式：{mode}")

    return None


def handle_stream(handler: Any, rt: Any, body: dict[str, Any]) -> None:
    mode = str(body.get("mode") or "target")
    copy_ctx = body.get("copyContext") if isinstance(body.get("copyContext"), dict) else None
    essence_sum = str(body.get("essenceSummary") or "")
    prof = body.get("profile") if isinstance(body.get("profile"), dict) else None
    gap_rep = body.get("gapReport") if isinstance(body.get("gapReport"), dict) else None
    use_cache = bool(body.get("useCache", True))
    active_league = _active_league(rt, body)

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "close")
    handler._write_cors()
    handler.end_headers()

    ev_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
    stop_flag = threading.Event()

    def _emit(ev_obj: dict[str, Any]) -> None:
        if not stop_flag.is_set():
            ev_queue.put(ev_obj)

    def _producer() -> None:
        nonlocal prof, gap_rep
        raw_code = ""
        pob_sandbox = None
        try:
            if mode in ("target", "copy"):
                raw_code, prof, pob_sandbox = prepare_llm_target_context(
                    rt,
                    body,
                    mode,
                    emit=_emit,
                    require_profile=False,
                )
            elif mode == "gap" and not gap_rep:
                my_code = body.get("myPobCode") or (rt.store.get_my_build() or {}).get("pobCode") or ""
                tgt_code = body.get("targetPobCode") or (rt.store.get_target_build() or {}).get("pobCode") or ""
                if my_code and tgt_code:
                    _emit({
                        "type": "tool_status",
                        "stage": "running",
                        "summary": "⚙️ 正在运行双码 PoB2 差距诊断...",
                    })
                    gap_rep = gap_analyzer.analyze_gap(
                        my_code,
                        tgt_code,
                        rt.pool(),
                        price_snapshot=rt.store.get_prices(),
                    )

            for ev in llm_advisor.stream_analyze_with_llm(
                mode,
                profile=prof,
                gap_report=gap_rep,
                copy_context=copy_ctx,
                price_snapshot=rt.store.get_prices(),
                essence_summary=essence_sum,
                league=active_league,
                state_dir=rt.store.state_dir,
                use_cache=use_cache,
                pob_sandbox=pob_sandbox,
            ):
                if stop_flag.is_set():
                    break
                etype = ev.get("type") or "delta"
                if mode == "target" and etype in ("structured", "done") and isinstance(ev.get("positioning"), dict) and raw_code:
                    persist_ai_positioning_if_matching(rt, raw_code, ev["positioning"])
                if etype == "done" and mode in ("copy", "consult", "target", "gap") and (ev.get("text") or ev.get("copyPlan")):
                    ev = _save_stream_plan_if_needed(rt, body, mode, raw_code, prof, copy_ctx, active_league, ev)
                _emit(ev)
        except Exception as stream_exc:  # noqa: BLE001
            _emit({"type": "error", "error": f"{type(stream_exc).__name__}: {stream_exc}"})
        finally:
            ev_queue.put(None)

    worker_t = threading.Thread(target=_producer, daemon=True)
    worker_t.start()

    try:
        while True:
            try:
                ev = ev_queue.get(timeout=12.0)
            except queue.Empty:
                handler.wfile.write(b": keepalive\n\n")
                handler.wfile.flush()
                continue
            if ev is None:
                break
            etype = ev.get("type") or "delta"
            payload_line = f"event: {etype}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            handler.wfile.write(payload_line.encode("utf-8"))
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        stop_flag.set()
    finally:
        stop_flag.set()
