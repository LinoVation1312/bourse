#!/usr/bin/env python3
"""Point d'entrée.

  python run.py gate            décide du mode (stdlib seulement) ; écrit mode=... dans $GITHUB_OUTPUT
  python run.py auto            choisit matin / watch / soir / skip selon l'heure de Paris
  python run.py matin|watch|soir|test|backtest
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


def gate():
    from bourse.schedule import decide
    mode = decide(dt.datetime.now(dt.timezone.utc), current_slots(), os.environ.get("MODE") or "auto")
    print(f"mode={mode}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"mode={mode}\n")


def main(mode, dry):
    from bourse import commands, jobs, state as ST
    from bourse.config import load_config, load_stats
    from bourse.schedule import decide
    from bourse.telegram import Telegram

    token, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")
    if not dry and not (token and chat):
        sys.exit("TG_TOKEN et TG_CHAT doivent être définis (secrets GitHub), ou utilise --dry.")
    now = dt.datetime.now(dt.timezone.utc)
    state = ST.load()
    if mode == "auto":
        mode = decide(now, state["slots"])
    if mode == "skip":
        print("Rien à faire à cette heure.")
        return
    cfg = load_config()
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
            for uid, text in ctx.tg.updates(state["offset"]):
                state["offset"] = max(state["offset"], uid)
                if text:
                    commands.handle(ctx, text)
            if mode == "matin":
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


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else "auto"
    if mode == "gate":
        gate()
    else:
        main(mode, "--dry" in sys.argv)
