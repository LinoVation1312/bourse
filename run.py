#!/usr/bin/env python3
"""Point d'entrée.

  python run.py gate            décide du mode (stdlib seulement) ; écrit mode=... dans $GITHUB_OUTPUT
  python run.py auto            choisit ouverture / watch / soir / skip selon l'heure de Paris
  python run.py ouverture|matin|watch|soir|flash|commands|test|backtest
  python run.py daemon          boucle permanente (Raspberry Pi, VPS…) : flash chaque minute, commandes en quelques secondes
  (commands : ne traite que les messages Telegram, sans rien analyser ; lancé par le webhook, voir README)
  Option --dry : rien n'est envoyé, les messages s'affichent dans le terminal (pas besoin de token Telegram)
"""
import os
import sys
import json
import datetime as dt
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def current_slots():
    try:
        return json.loads((ROOT / "state.json").read_text(encoding="utf-8")).get("slots", {})
    except (OSError, ValueError):
        return {}


def current_cfg():
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def webhook_update(chat):
    """Message transmis par le webhook (variable TG_UPDATE, JSON) -> [(update_id, texte)] ; vide sinon."""
    raw = os.environ.get("TG_UPDATE", "").strip()
    if not raw or raw == "null":
        return []
    try:
        u = json.loads(raw)
        msg = u.get("message") or {}
        ok = str(msg.get("chat", {}).get("id")) == str(chat)
        return [(int(u.get("update_id", 0)), msg.get("text", "") if ok else None)]
    except (ValueError, AttributeError, TypeError):
        return []


def gate():
    from bourse.schedule import decide
    mode = decide(dt.datetime.now(dt.timezone.utc), current_slots(), os.environ.get("MODE") or "auto", current_cfg())
    print(f"mode={mode}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"mode={mode}\n")


def main(mode, dry, poll=0):
    from bourse import commands, jobs, state as ST
    from bourse.config import load_config, load_stats
    from bourse.schedule import decide
    from bourse.telegram import Telegram

    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if not dry and not (token and chat):
        sys.exit("TG_TOKEN et TG_CHAT doivent être définis (secrets GitHub), ou utilise --dry.")
    now = dt.datetime.now(dt.timezone.utc)
    state = ST.load()
    cfg = load_config()
    if mode == "auto":
        mode = decide(now, state["slots"], cfg=cfg)
    if mode == "skip":
        print("Rien à faire à cette heure.")
        return
    if not cfg.get("traduire_actus", True):
        os.environ["BOURSE_NO_TRANSLATE"] = "1"
    ctx = jobs.Ctx(cfg=cfg, state=state, stats=load_stats(), tg=Telegram(token or "", chat or "", dry),
                   now=now, finnhub=os.environ.get("FINNHUB_KEY"))
    try:
        if mode == "backtest":
            from bourse import backtest, trend
            st = backtest.run(ctx.cfg, now)
            st["tendance"] = trend.run_backtest(ctx.cfg, now)
            (ROOT / "stats.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            (ROOT / "report.md").write_text(backtest.report_md(st) + "\n\n" + trend.report_md(st["tendance"]), encoding="utf-8")
            ctx.tg.send(backtest.telegram_summary(st))
            ctx.tg.send(trend.telegram_summary(st["tendance"]))
        else:
            pending = webhook_update(ctx.tg.chat) + ctx.tg.updates(state["offset"], poll)
            for uid, text in sorted(pending, key=lambda x: x[0]):
                if uid and uid <= state["offset"]:
                    continue                                  # déjà traité (webhook + relève getUpdates)
                state["offset"] = max(state["offset"], uid)
                if text:
                    commands.handle(ctx, text)
            if mode == "commands":
                pass
            elif mode == "flash":
                jobs.flash(ctx)
            elif mode == "ouverture":
                jobs.ouverture(ctx)
            elif mode == "matin":
                jobs.matin(ctx)
            elif mode == "soir":
                jobs.soir(ctx)
            elif mode == "watch":
                jobs.watch(ctx)
            elif mode == "test":
                jobs.test(ctx)
            else:
                sys.exit(f"Mode inconnu : {mode}")
    except Exception as e:
        traceback.print_exc()
        if ST.fresh(state, "warn|crash", now, 6):
            from bourse.fmt import esc
            ctx.tg.send(f"⚠️ Erreur du bot ({esc(type(e).__name__)} : {esc(str(e)[:200])}). Détails dans l'onglet Actions de GitHub.")
    finally:
        if not dry:
            ST.prune(state, now)
            ST.save(state)


def next_mode(mode, last, now_s, every_flash=60, every_watch=300):
    """Mode du prochain tour de la boucle permanente : un briefing dû passe avant tout ; sinon watch / flash à leur
    rythme ; sinon on écoute seulement Telegram (long polling : réponse aux commandes en quelques secondes)."""
    if mode in ("ouverture", "soir", "matin"):
        return mode
    if mode == "watch" and now_s - last.get("watch", 0) >= every_watch:
        return "watch"
    if mode in ("watch", "flash") and now_s - last.get("flash", 0) >= every_flash:
        return "flash"
    return "commands"


def daemon(dry=False):
    import time
    from bourse.schedule import decide
    from bourse import flash as F
    last = {}
    print("Boucle permanente démarrée (Ctrl+C pour arrêter).")
    while True:
        cfg, now_s = current_cfg(), time.time()
        mode = decide(dt.datetime.now(dt.timezone.utc), current_slots(), cfg=cfg)
        chosen = next_mode(mode, last, now_s, F.conf(cfg)["intervalle_s"], cfg.get("alertes", {}).get("intervalle_watch_s", 300))
        if chosen in ("watch", "flash"):
            last["flash"] = now_s
        if chosen == "watch":
            last["watch"] = now_s
        try:
            main(chosen, dry, poll=10 if chosen == "commands" else 0)
        except SystemExit:
            raise
        except Exception:
            traceback.print_exc()
            time.sleep(5)
        if chosen in ("ouverture", "soir", "matin"):
            time.sleep(45)                    # si les données manquent, on réessaie dans 45 s au lieu de marteler Yahoo


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else "auto"
    if mode == "gate":
        gate()
    elif mode == "daemon":
        daemon("--dry" in sys.argv)
    else:
        main(mode, "--dry" in sys.argv)
