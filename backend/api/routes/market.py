"""市集核价、价格快照、做装决策、词典元数据与扩展搜货通道路由。"""
from __future__ import annotations

import time
import urllib.parse
import webbrowser
from typing import Any, Optional

from backend.data import affix_updater, game_terms, patch_dict_updater
from backend.domain import ablation_dossier, trade_url
from backend.domain.crafting import affix_pool, compare as craft_compare
from backend.engine import pob_parser, runefix
from backend.market import market_pricer, ninja_rates


def handle_get(rt: Any, path: str, raw_url: str) -> Optional[tuple[int, dict[str, Any]]]:
    if path == "/prices":
        return 200, {"ok": True, **rt.store.get_prices()}

    if path == "/extension/trade-requests":
        pending_only = "all=1" not in raw_url
        reqs = rt.store.get_trade_requests(pending_only=pending_only)
        return 200, {"ok": True, "requests": reqs}

    if path == "/market/rates":
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_url).query)
        league = (qs.get("league") or ["Standard"])[0]
        refresh = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
        rates = ninja_rates.fetch_ninja_rates(league, force_refresh=refresh)
        return 200, {"ok": True, "rates": rates}

    if path == "/market/leagues":
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_url).query)
        refresh = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
        leagues = market_pricer.fetch_active_leagues(force_refresh=refresh, state_dir=rt.store.state_dir)
        return 200, {"ok": True, "leagues": leagues}

    if path == "/craft/library-status":
        return 200, {"ok": True, "status": affix_updater.get_library_status()}

    if path == "/meta/patch-dict":
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_url).query)
        refresh = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
        if refresh:
            pdict = patch_dict_updater.write_patch_dict_files(write_backend=True, write_extension=False)
        else:
            pdict = patch_dict_updater.load_patch_dict(auto_build=True)
        return 200, {"ok": True, "patchDict": pdict}

    if path == "/meta/game-terms":
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_url).query)
        refresh = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
        if refresh:
            game_terms.update_game_terms_files(write_repo=False)
        terms = game_terms.load_game_terms()
        mods = game_terms.load_mods_zh_map()
        return 200, {
            "ok": True,
            "generatedAt": terms.get("generatedAt", ""),
            "terms": terms,
            "mods": mods,
        }

    if path == "/craft/meta":
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_url).query)
        slot = (qs.get("slot") or ["Rings"])[0]
        base_type = (qs.get("baseType") or [""])[0]
        league = (qs.get("league") or ["Standard"])[0]
        try:
            ilvl = int((qs.get("ilvl") or ["82"])[0])
        except ValueError:
            ilvl = 82
        rates = ninja_rates.fetch_ninja_rates(league)
        item_class = affix_pool.resolve_item_class(slot, base_type)
        return 200, {
            "ok": True,
            "slot": slot,
            "itemClass": item_class,
            "supportedClasses": affix_pool.list_supported_classes(),
            "affixOptions": affix_pool.list_affix_options_for_slot(slot, ilvl=ilvl, base_type=base_type),
            "essences": affix_pool.list_essences_for_slot(slot, base_type=base_type, rates=rates),
            "rates": rates,
            "libraryStatus": affix_updater.get_library_status(),
        }

    return None


def _resolve_league(rt: Any, body: dict[str, Any]) -> str:
    return str(
        body.get("league")
        or (rt.store.get_target_build() or {}).get("league")
        or (rt.store.get_prices() or {}).get("league")
        or market_pricer.default_trade_league(state_dir=rt.store.state_dir)
    ).strip()


def handle_post(rt: Any, path: str, body: dict[str, Any]) -> Optional[tuple[int, dict[str, Any]]]:
    if path == "/prices":
        saved = rt.store.save_prices(body)
        rt.bus.publish("prices_updated", {"count": saved["count"], "updatedAt": saved["updatedAt"]})
        return 200, {"ok": True, "prices": saved}

    if path == "/prices/override":
        slot_name = str(body.get("slot") or "").strip()
        if not slot_name:
            raise ValueError("缺少 slot")
        val = float(body.get("divineValue") or 0.0)
        if val <= 0:
            raise ValueError("价格必须大于 0")
        eff_league = _resolve_league(rt, body)
        saved = market_pricer.merge_price_entries(
            rt.store,
            league=eff_league,
            new_entries=[{
                "slot": slot_name,
                "itemName": str(body.get("itemName") or ""),
                "kind": str(body.get("kind") or "exact"),
                "divineValue": round(val, 2),
                "currencyLabel": market_pricer.format_divine_label(val),
                "source": "manual",
                "note": "手动填价",
                "updatedAt": int(time.time() * 1000),
            }],
            source="manual",
        )
        rt.bus.publish("prices_updated", {"count": saved["count"], "updatedAt": saved["updatedAt"]})
        return 200, {"ok": True, "prices": saved}

    if path == "/market/price-ninja":
        raw = body.get("xml") or body.get("pobCode") or (rt.store.get_target_build() or {}).get("pobCode") or ""
        if not raw:
            raise ValueError("缺少目标 BD PoB2 分享码")
        eff_league = _resolve_league(rt, body)
        force_refresh = bool(body.get("forceRefresh", False))
        xml = pob_parser.decode_pob(raw)
        norm_xml, _ = runefix.normalize_runes(xml)
        parsed = pob_parser.parse_pob_xml(norm_xml)
        ninja_idx = market_pricer.fetch_ninja_item_index(
            eff_league,
            force_refresh=force_refresh,
            state_dir=rt.store.state_dir,
        )
        matched = market_pricer.match_build_ninja_prices(parsed, ninja_idx)
        saved = market_pricer.merge_price_entries(
            rt.store,
            league=ninja_idx.get("league") or eff_league,
            new_entries=matched,
            source="poe.ninja",
        )
        rt.bus.publish("prices_updated", {"count": saved["count"], "updatedAt": saved["updatedAt"]})
        return 200, {
            "ok": True,
            "league": ninja_idx.get("league") or eff_league,
            "matchedCount": len(matched),
            "exaltPerDivine": ninja_idx.get("exaltPerDivine"),
            "chaosPerDivine": ninja_idx.get("chaosPerDivine"),
            "itemCount": ninja_idx.get("itemCount", 0),
            "entries": matched,
            "prices": saved,
        }

    if path == "/market/price-item":
        raw = body.get("xml") or body.get("pobCode") or (rt.store.get_target_build() or {}).get("pobCode") or ""
        slot_name = str(body.get("slot") or "").strip()
        if not raw or not slot_name:
            raise ValueError("需要提供目标 BD 与 slot")
        eff_league = _resolve_league(rt, body)
        kind = str(body.get("kind") or "budget").strip().lower()
        xml = pob_parser.decode_pob(raw)
        norm_xml, _ = runefix.normalize_runes(xml)
        parsed = pob_parser.parse_pob_xml(norm_xml)
        all_its = list(parsed.get("equippedItems") or []) + list(parsed.get("jewels") or [])
        target_it = next((it for it in all_its if str(it.get("slot") or "").lower() == slot_name.lower()), None)
        if target_it is None:
            raise ValueError(f"目标 BD 中找不到槽位：{slot_name}")
        ninja_idx = market_pricer.fetch_ninja_item_index(eff_league, state_dir=rt.store.state_dir)
        cached_dossier = ablation_dossier.get_cached_dossier_by_build(raw, state_dir=rt.store.state_dir)
        dossier_item = None
        if isinstance(cached_dossier, dict):
            dossier_item = next(
                (
                    di for di in list(cached_dossier.get("items") or []) + list(cached_dossier.get("jewels") or [])
                    if str(di.get("slot") or "").lower() == slot_name.lower()
                ),
                None,
            )
        res = market_pricer.price_single_item_trade2(
            target_it,
            league=eff_league,
            kind=kind,
            parsed_build=parsed,
            dossier_item=dossier_item,
            ninja_index=ninja_idx,
        )
        saved = rt.store.get_prices()
        if res.get("quote"):
            saved = market_pricer.merge_price_entries(
                rt.store,
                league=eff_league,
                new_entries=[res["quote"]],
                source="backend-trade",
            )
            rt.bus.publish("prices_updated", {"count": saved["count"], "updatedAt": saved["updatedAt"]})
        return 200, {
            "ok": True,
            "queryOk": bool(res.get("ok")),
            "rateLimited": bool(res.get("rateLimited")),
            "quote": res.get("quote"),
            "totalListings": int(res.get("totalListings") or 0),
            "tradeUrl": res.get("tradeUrl") or "",
            "weightedTradeUrl": res.get("weightedTradeUrl") or res.get("tradeUrl") or "",
            "weightedTerms": res.get("weightedTerms") or [],
            "targetScore": res.get("targetScore") or 0.0,
            "minScore": res.get("minScore") or 0.0,
            "relaxed": bool(res.get("relaxed")),
            "message": res.get("message") or res.get("error") or "",
            "prices": saved,
        }

    if path == "/market/price-batch":
        raw = body.get("xml") or body.get("pobCode") or (rt.store.get_target_build() or {}).get("pobCode") or ""
        if not raw:
            raise ValueError("缺少目标 BD PoB2 分享码")
        eff_league = _resolve_league(rt, body)
        kind = str(body.get("kind") or "budget").strip().lower()
        slots = body.get("slots") if isinstance(body.get("slots"), list) else None
        xml = pob_parser.decode_pob(raw)
        norm_xml, _ = runefix.normalize_runes(xml)
        parsed = pob_parser.parse_pob_xml(norm_xml)
        cached_dossier = ablation_dossier.get_cached_dossier_by_build(raw, state_dir=rt.store.state_dir)
        ninja_idx = market_pricer.fetch_ninja_item_index(eff_league, state_dir=rt.store.state_dir)
        ninja_entries = market_pricer.match_build_ninja_prices(parsed, ninja_idx)
        if ninja_entries:
            market_pricer.merge_price_entries(rt.store, league=eff_league, new_entries=ninja_entries, source="poe.ninja")
        batch_res = market_pricer.price_build_trade2_batch(
            parsed,
            league=eff_league,
            kind=kind,
            slots=slots,
            dossier=cached_dossier,
            ninja_index=ninja_idx,
            store=rt.store,
        )
        saved = rt.store.get_prices()
        rt.bus.publish("prices_updated", {"count": saved.get("count", 0), "updatedAt": saved.get("updatedAt", 0)})
        return 200, {
            "ok": True,
            "ninjaMatchedCount": len(ninja_entries),
            **batch_res,
            "prices": saved,
        }

    if path == "/extension/open-trade":
        req = rt.store.add_trade_request(body)
        rt.bus.publish("trade_open_requested", req)
        return 200, {"ok": True, "request": req}

    if path == "/extension/trade-requests/ack":
        req_id = str(body.get("id") or "")
        ok = rt.store.ack_trade_request(req_id)
        return 200, {"ok": ok, "id": req_id}

    if path == "/system/open-url":
        target_url = str(body.get("url") or "").strip()
        parsed_u = urllib.parse.urlparse(target_url)
        if parsed_u.scheme not in ("http", "https") or not parsed_u.netloc:
            raise ValueError("仅允许打开合法的 http/https 网址")
        opened = bool(webbrowser.open(target_url, new=2))
        return 200, {"ok": True, "opened": opened, "url": target_url}

    if path == "/trade/build-url":
        tgt_obj = rt.store.get_target_build() or {}
        tgt_league = tgt_obj.get("league") or ""
        price_league = (rt.store.get_prices() or {}).get("league") or ""
        req_league = str(body.get("league") or tgt_league or price_league or "Standard")
        use_weighted = bool(body.get("useWeighted", False))
        req_slot = str(body.get("slot") or "").strip()
        req_action = str(body.get("action") or "buy_budget").strip().lower()
        raw_pob = str(body.get("pobCode") or tgt_obj.get("pobCode") or "").strip()

        if use_weighted and req_slot and raw_pob:
            try:
                xml = pob_parser.decode_pob(raw_pob)
                norm_xml, _ = runefix.normalize_runes(xml)
                parsed = pob_parser.parse_pob_xml(norm_xml)
                all_its = list(parsed.get("equippedItems") or []) + list(parsed.get("jewels") or [])
                target_it = next((it for it in all_its if str(it.get("slot") or "").lower() == req_slot.lower()), None)
                if target_it is not None:
                    cached_dossier = ablation_dossier.get_cached_dossier_by_build(raw_pob, state_dir=rt.store.state_dir)
                    d_item = None
                    if isinstance(cached_dossier, dict):
                        d_item = next(
                            (
                                di for di in list(cached_dossier.get("items") or []) + list(cached_dossier.get("jewels") or [])
                                if str(di.get("slot") or "").lower() == req_slot.lower()
                            ),
                            None,
                        )
                    kind_map = {
                        "buy_finished": "exact",
                        "buy_budget": "budget",
                        "craft_semi": "semi",
                        "buy_base": "base",
                        "buy_unique": "exact",
                    }
                    rev_spec = ablation_dossier.ensure_reverse_spec(
                        raw_pob,
                        rt.pool(),
                        dossier=cached_dossier if isinstance(cached_dossier, dict) else None,
                        state_dir=rt.store.state_dir,
                    )
                    slot_spec = None
                    if isinstance(rev_spec, dict) and isinstance(rev_spec.get("slotSpecs"), dict):
                        slot_spec = next(
                            (v for k, v in rev_spec["slotSpecs"].items() if str(k).lower() == req_slot.lower()),
                            None,
                        )
                    w_built = trade_url.build_weighted_substitute_search(
                        target_it,
                        league=req_league,
                        kind=kind_map.get(req_action, "budget"),
                        parsed_build=parsed,
                        dossier_item=d_item,
                        slot_spec=slot_spec,
                    )
                    return 200, {"ok": True, "resolvedAffixes": [], **w_built}
            except Exception:
                pass

        built = trade_url.build_trade_search(
            league=req_league,
            slot=req_slot,
            item_name=str(body.get("itemName") or ""),
            base_type=str(body.get("baseType") or ""),
            rarity=str(body.get("rarity") or "RARE"),
            action=req_action,
            lock_base=body.get("lockBase") if isinstance(body.get("lockBase"), bool) else None,
            affixes=body.get("affixes") if isinstance(body.get("affixes"), list) else [],
            min_ilvl=int(body["minIlvl"]) if isinstance(body.get("minIlvl"), (int, float)) else None,
            min_sockets=int(body["minSockets"]) if isinstance(body.get("minSockets"), (int, float)) else None,
            corrupted=body.get("corrupted") if isinstance(body.get("corrupted"), bool) else None,
            max_divine=float(body["maxDivine"]) if isinstance(body.get("maxDivine"), (int, float)) else None,
        )
        return 200, {"ok": True, **built}

    if path == "/craft/compare":
        decision = craft_compare.compare_craft_vs_buy(body)
        return 200, {"ok": True, "decision": decision}

    if path == "/craft/update-library":
        upd = affix_updater.update_all_libraries(
            write_repo=bool(body.get("writeRepo", False)),
            update_repoe=bool(body.get("updateRepoe", True)),
            update_trade_stats=bool(body.get("updateTradeStats", True)),
        )
        if not upd.get("ok"):
            return 502, {"ok": False, "error": "; ".join(upd.get("errors") or ["更新失败"]), "result": upd}
        return 200, {"ok": True, "result": upd, "status": affix_updater.get_library_status()}

    return None
