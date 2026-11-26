"""构筑解析、消融账本、天赋树、双码差距诊断、路线规划与抄作业方案路由。"""
from __future__ import annotations

import urllib.parse
from typing import Any, Optional

from backend.domain import ablation_dossier, budget_planner, build_profile, gap_analyzer
from backend.engine import pob_parser, runefix, tree_index
from backend.market.price_store import pob_fingerprint


def sanitize_target_positioning(target: dict[str, Any] | None) -> dict[str, Any] | None:
    """过滤非 AI 产出的静态正则评级（仅保留由 AI 结合 PoB2 引擎实算数据产出的 contentReadiness）。"""
    if not isinstance(target, dict):
        return target
    pos = target.get("positioning")
    if not isinstance(pos, dict):
        return target
    cr = pos.get("contentReadiness")
    if isinstance(cr, dict):
        is_ai = str(cr.get("confidence") or "").lower() == "ai" or str(pos.get("analyzedBy") or "").lower() == "ai"
        if not is_ai:
            out = dict(target)
            pos_clean = dict(pos)
            pos_clean["contentReadiness"] = None
            out["positioning"] = pos_clean
            return out
    return target


def persist_ai_positioning_if_matching(
    rt: Any,
    raw_code: str,
    positioning: dict[str, Any] | None,
) -> None:
    """当 AI 完成目标 BD 评级/拆解后，将其持久化回当前匹配的目标 BD 记录，避免刷新回退。"""
    if not raw_code or not isinstance(positioning, dict):
        return
    cur = rt.store.get_target_build()
    if not isinstance(cur, dict) or (cur.get("pobCode") or "").strip() != raw_code.strip():
        return
    updated = {
        **cur,
        "positioning": positioning,
    }
    if positioning.get("archetype"):
        updated["title"] = cur.get("title") or positioning["archetype"]
    rt.store.save_target_build(updated)


def handle_get(rt: Any, path: str, raw_url: str) -> Optional[tuple[int, dict[str, Any]]]:
    if path == "/builds/target":
        tgt = sanitize_target_positioning(rt.store.get_target_build())
        return 200, {"ok": True, "target": tgt}
    if path == "/builds/mine":
        mine = rt.store.get_my_build()
        return 200, {"ok": True, "mine": mine}
    if path == "/copy-plans":
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_url).query)
        pob_hash = (qs.get("pobHash") or [""])[0].strip() or None
        mode_q = (qs.get("mode") or [""])[0].strip() or None
        plans = rt.store.list_copy_plans(pob_hash=pob_hash, mode=mode_q)
        return 200, {"ok": True, "plans": plans}
    if path == "/tree/layout":
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_url).query)
        ver = (qs.get("version") or [""])[0].strip() or None
        try:
            layout = tree_index.load_tree_layout(ver)
            return 200, {"ok": True, "layout": layout}
        except FileNotFoundError as exc:
            return 404, {"ok": False, "error": str(exc)}
    return None


def handle_post(rt: Any, path: str, body: dict[str, Any]) -> Optional[tuple[int, dict[str, Any]]]:
    if path == "/builds/parse":
        raw = body.get("xml") or body.get("pobCode") or ""
        parsed = pob_parser.parse_pob_code(raw)
        return 200, {"ok": True, "build": parsed}

    if path == "/builds/profile":
        raw = body.get("xml") or body.get("pobCode") or ""
        run_sim = bool(body.get("runSimulation", False))
        run_ablation = bool(body.get("runAblation", run_sim))
        use_ai = bool(body.get("useAi", False))
        price_snap = rt.store.get_prices() if body.get("includePrices", True) else None
        cached_sim = (
            ablation_dossier.get_cached_base_sim(raw, state_dir=rt.store.state_dir)
            if (run_sim and not run_ablation and raw)
            else None
        )
        if run_sim and not run_ablation and cached_sim is None and raw:
            xml_fresh = pob_parser.decode_pob(raw)
            norm_fresh, _ = runefix.normalize_runes(xml_fresh)
            cached_sim = rt.pool().simulate(norm_fresh, include_audit=True)
            ablation_dossier.update_cached_base_sim(raw, cached_sim, state_dir=rt.store.state_dir)
        need_pool = run_sim and (run_ablation or cached_sim is None)
        profile = build_profile.extract_profile(
            raw,
            pool=rt.pool() if need_pool else None,
            run_simulation=run_sim,
            run_ablation=run_ablation,
            cached_base_sim=cached_sim,
            price_snapshot=price_snap if price_snap and not price_snap.get("empty") else None,
            use_ai=use_ai,
            state_dir=rt.store.state_dir,
        )
        return 200, {"ok": True, "profile": profile}

    if path == "/builds/ablation-dossier":
        raw = body.get("xml") or body.get("pobCode") or (rt.store.get_target_build() or {}).get("pobCode") or ""
        if not raw:
            raise ValueError("缺少目标 BD PoB2 分享码")
        price_snap = body.get("priceSnapshot") or (rt.store.get_prices() if body.get("includePrices", True) else None)
        overrides = body.get("priceOverrides") if isinstance(body.get("priceOverrides"), dict) else None
        force_refresh = bool(body.get("forceRefresh", False))
        req_league = str(
            body.get("league")
            or (rt.store.get_target_build() or {}).get("league")
            or (price_snap or {}).get("league")
            or ""
        ).strip()
        sf_key = f"dossier|{pob_fingerprint(raw)}|{req_league}|{force_refresh}|{sorted((overrides or {}).items())}"
        dossier = rt.run_singleflight(
            sf_key,
            lambda: ablation_dossier.build_ablation_dossier(
                raw,
                pool=None,
                pool_factory=rt.pool,
                price_snapshot=price_snap if price_snap and not price_snap.get("empty") else None,
                price_overrides=overrides,
                league=req_league,
                state_dir=rt.store.state_dir,
                force_refresh=force_refresh,
            ),
        )
        return 200, {"ok": True, "dossier": dossier}

    if path == "/builds/passive-tree":
        tgt_code = (
            body.get("targetPobCode")
            or body.get("pobCode")
            or (rt.store.get_target_build() or {}).get("pobCode")
            or ""
        )
        if not tgt_code:
            raise ValueError("缺少目标 BD PoB2 分享码")
        my_code = body.get("myPobCode")
        if my_code is None:
            my_code = (rt.store.get_my_build() or {}).get("pobCode") or ""
        char_level = body.get("characterLevel")
        lvl_int = int(char_level) if isinstance(char_level, (int, float)) and int(char_level) > 0 else None
        cut_notables = body.get("cutNotables") if isinstance(body.get("cutNotables"), list) else None
        plan = tree_index.build_passive_tree_plan(
            tgt_code,
            my_pob_code=str(my_code or "") or None,
            character_level=lvl_int,
            cut_notables=[str(x) for x in cut_notables] if cut_notables else None,
        )
        return 200, {"ok": True, "plan": plan}

    if path == "/builds/target":
        payload = dict(body)
        if not payload.get("positioning") and payload.get("pobCode"):
            try:
                prof = build_profile.extract_profile(
                    payload["pobCode"],
                    run_simulation=False,
                    price_snapshot=rt.store.get_prices(),
                )
                payload["positioning"] = prof["positioning"]
                if not payload.get("title"):
                    payload["title"] = prof["positioning"].get("archetype") or ""
            except Exception:
                pass
        payload = sanitize_target_positioning(payload) or payload
        saved = rt.store.save_target_build(payload)
        rt.bus.publish("target_updated", {
            "title": saved.get("title"),
            "pushedAt": saved.get("pushedAt"),
            "archetype": (saved.get("positioning") or {}).get("archetype", ""),
        })
        return 200, {"ok": True, "target": saved}

    if path == "/builds/mine":
        payload = dict(body)
        if not payload.get("title") and payload.get("pobCode"):
            try:
                prof = build_profile.extract_profile(payload["pobCode"], run_simulation=False)
                payload["title"] = prof["positioning"].get("archetype") or ""
            except Exception:
                pass
        saved = rt.store.save_my_build(payload)
        return 200, {"ok": True, "mine": saved}

    if path == "/copy-plans":
        saved = rt.store.save_copy_plan(body)
        return 200, {"ok": True, "plan": saved}

    if path == "/copy-plans/item-state":
        plan_id = str(body.get("planId") or "").strip()
        item_key = str(body.get("itemKey") or "").strip()
        patch = body.get("patch") if isinstance(body.get("patch"), dict) else None
        if not plan_id or not item_key or patch is None:
            raise ValueError("需要提供 planId、itemKey 与 patch")
        updated = rt.store.update_copy_plan_item_state(plan_id, item_key, patch)
        if updated is None:
            return 404, {"ok": False, "error": f"找不到已保存的抄作业方案：{plan_id}"}
        return 200, {"ok": True, "plan": updated}

    if path == "/copy-plans/delete":
        plan_id = str(body.get("id") or "").strip()
        if not plan_id:
            raise ValueError("缺少 id")
        deleted = rt.store.delete_copy_plan(plan_id)
        return 200, {"ok": True, "deleted": deleted, "id": plan_id}

    if path == "/gap/analyze":
        my_code = body.get("myPobCode") or (rt.store.get_my_build() or {}).get("pobCode") or ""
        tgt_code = body.get("targetPobCode") or (rt.store.get_target_build() or {}).get("pobCode") or ""
        if not my_code or not tgt_code:
            raise ValueError("需要同时提供「我的 BD」与「目标 BD」PoB2 分享码")
        price_snap = body.get("priceSnapshot") or rt.store.get_prices()
        overrides = body.get("priceOverrides") if isinstance(body.get("priceOverrides"), dict) else None
        report = gap_analyzer.analyze_gap(
            my_code,
            tgt_code,
            rt.pool(),
            price_snapshot=price_snap if price_snap and not price_snap.get("empty") else None,
            price_overrides=overrides,
        )
        return 200, {"ok": True, "report": report}

    if path == "/gap/slot-affixes":
        my_code = body.get("myPobCode") or (rt.store.get_my_build() or {}).get("pobCode") or ""
        tgt_code = body.get("targetPobCode") or (rt.store.get_target_build() or {}).get("pobCode") or ""
        slot_name = str(body.get("slot") or "").strip()
        if not my_code or not tgt_code or not slot_name:
            raise ValueError("需要提供 myPobCode、targetPobCode 与 slot")
        report = gap_analyzer.analyze_slot_affixes(my_code, tgt_code, slot_name, rt.pool())
        return 200, {"ok": True, "report": report}

    if path == "/roadmap":
        budget_d = float(body.get("budgetDivine") or 0.0)
        overrides = body.get("priceOverrides") if isinstance(body.get("priceOverrides"), dict) else None
        zero_steps = body.get("zeroCostSteps")
        slot_steps = body.get("slotUpgrades")
        my_code = body.get("myPobCode") or (rt.store.get_my_build() or {}).get("pobCode") or ""
        tgt_code = body.get("targetPobCode") or (rt.store.get_target_build() or {}).get("pobCode") or ""
        base_sim_for_combo = None
        if slot_steps is None:
            if not my_code or not tgt_code:
                raise ValueError("需要传入 slotUpgrades 或提供双码进行实时分析")
            gap = gap_analyzer.analyze_gap(
                my_code,
                tgt_code,
                rt.pool(),
                price_snapshot=rt.store.get_prices(),
                price_overrides=overrides,
            )
            zero_steps = gap["zeroCostSteps"]
            slot_steps = gap["slotUpgrades"]
            base_sim_for_combo = gap.get("myStats")
        want_verify = body.get("verifyCombo")
        combo_pool = (
            rt.pool()
            if (my_code and tgt_code and (want_verify is True or (want_verify is not False and rt._pool is not None)))
            else None
        )
        roadmap = budget_planner.plan_roadmap(
            zero_steps or [],
            slot_steps or [],
            budget_d,
            price_overrides=overrides,
            my_code=my_code or None,
            target_code=tgt_code or None,
            pool=combo_pool,
            base_sim=base_sim_for_combo,
        )
        return 200, {"ok": True, "roadmap": roadmap}

    if path == "/simulate":
        raw = body.get("xml") or body.get("pobCode") or ""
        xml = pob_parser.decode_pob(raw)
        xml, fixed = runefix.normalize_runes(xml)
        res = rt.pool().simulate(
            xml,
            lock_dps_key=body.get("lockDpsKey"),
            include_audit=bool(body.get("includeAudit", True)),
        )
        return 200, {"ok": True, "fixedRunes": fixed, "result": res}

    return None
