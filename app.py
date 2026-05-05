"""
Twitch Bot — Backend Flask (Multi-Canal)
Supporte plusieurs canaux simultanément, avec ajout/suppression en direct.
"""

import threading
import asyncio
import json
import os
from datetime import datetime
from flask import Flask, render_template, jsonify, request
import twitchio
from twitchio.ext import commands

app = Flask(__name__)

# ────────────────────────────────────────────────
#  État global
# ────────────────────────────────────────────────
bot_instance = None
bot_thread   = None
bot_loop     = None
bot_running  = False
logs         = []

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "token":           "",
    "channels":        [],
    "lurk_message":    "{user} part en mode lurk dans les buissons... Merci pour le support ! 👀🌿",
    "unlurk_message":  "👋 {user} sort des buissons ! Bienvenue de retour !"
}

# ────────────────────────────────────────────────
#  Config (persistante)
# ────────────────────────────────────────────────
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            saved = json.load(f)
            if "channel" in saved and "channels" not in saved:
                saved["channels"] = [saved.pop("channel")]
            return {**DEFAULT_CONFIG, **saved}
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

config = load_config()

# ────────────────────────────────────────────────
#  Logs
# ────────────────────────────────────────────────
def add_log(msg: str, level: str = "info"):
    entry = {"time": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level}
    logs.append(entry)
    if len(logs) > 300:
        logs.pop(0)
    print(f"[{entry['time']}] [{level.upper()}] {msg}")

# ────────────────────────────────────────────────
#  Bot Twitch
# ────────────────────────────────────────────────
class TwitchBot(commands.Bot):

    def __init__(self, token, channels, lurk_msg, unlurk_msg):
        super().__init__(
            token=token,
            prefix="!",
            initial_channels=channels
        )
        self.lurk_msg   = lurk_msg
        self.unlurk_msg = unlurk_msg

    async def event_ready(self):
        add_log(f"Connecté en tant que {self.nick}", "success")
        for ch in self.connected_channels:
            add_log(f"Rejoint #{ch.name}", "info")

    async def event_message(self, message: twitchio.Message):
        if message.echo:
            return
        await self.handle_commands(message)

    @commands.command(name="lurk")
    async def lurk(self, ctx: commands.Context):
        msg = self.lurk_msg.format(user=ctx.author.name)
        await ctx.send(msg)
        add_log(f"#{ctx.channel.name} — {ctx.author.name} → !lurk", "command")

    @commands.command(name="unlurk")
    async def unlurk(self, ctx: commands.Context):
        msg = self.unlurk_msg.format(user=ctx.author.name)
        await ctx.send(msg)
        add_log(f"#{ctx.channel.name} — {ctx.author.name} → !unlurk", "command")

    async def event_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        add_log(f"Erreur : {error}", "error")


def _run_bot_thread(token, channels, lurk_msg, unlurk_msg):
    global bot_instance, bot_loop, bot_running
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    bot_instance = TwitchBot(token, channels, lurk_msg, unlurk_msg)
    try:
        bot_running = True
        add_log("Démarrage du bot...", "info")
        bot_instance.run()
    except Exception as e:
        add_log(f"Erreur fatale : {e}", "error")
    finally:
        bot_running = False
        add_log("Bot arrêté.", "warning")


# ────────────────────────────────────────────────
#  Routes Flask
# ────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start():
    global bot_thread, config
    data = request.get_json() or {}
    if not data.get("token"):
        return jsonify({"error": "Token OAuth requis."}), 400
    channels = [c.lstrip("#").lower().strip() for c in data.get("channels", []) if c.strip()]
    if not channels:
        return jsonify({"error": "Au moins un canal est requis."}), 400
    if bot_thread and bot_thread.is_alive():
        return jsonify({"error": "Le bot est déjà actif."}), 400

    config.update({
        "token":          data["token"],
        "channels":       channels,
        "lurk_message":   data.get("lurk_message",   DEFAULT_CONFIG["lurk_message"]),
        "unlurk_message": data.get("unlurk_message",  DEFAULT_CONFIG["unlurk_message"])
    })
    save_config(config)
    bot_thread = threading.Thread(
        target=_run_bot_thread,
        args=(config["token"], config["channels"], config["lurk_message"], config["unlurk_message"]),
        daemon=True
    )
    bot_thread.start()
    return jsonify({"status": "starting"})


@app.route("/api/stop", methods=["POST"])
def stop():
    global bot_instance, bot_loop, bot_running
    if bot_instance and bot_loop and not bot_loop.is_closed():
        asyncio.run_coroutine_threadsafe(bot_instance.close(), bot_loop)
        add_log("Arrêt demandé.", "warning")
    else:
        bot_running = False
    return jsonify({"status": "stopping"})


@app.route("/api/channels/add", methods=["POST"])
def add_channel():
    global config
    data = request.get_json() or {}
    channel = data.get("channel", "").lstrip("#").lower().strip()
    if not channel:
        return jsonify({"error": "Nom de canal invalide."}), 400
    if channel in config["channels"]:
        return jsonify({"error": f"#{channel} est déjà dans la liste."}), 400

    config["channels"].append(channel)
    save_config(config)

    if bot_instance and bot_loop and bot_running and not bot_loop.is_closed():
        asyncio.run_coroutine_threadsafe(bot_instance.join_channels([channel]), bot_loop)
        add_log(f"Rejoint #{channel} en direct", "success")
    else:
        add_log(f"#{channel} ajouté à la liste", "info")

    return jsonify({"status": "added", "channels": config["channels"]})


@app.route("/api/channels/remove", methods=["POST"])
def remove_channel():
    global config
    data = request.get_json() or {}
    channel = data.get("channel", "").lstrip("#").lower().strip()
    if channel not in config["channels"]:
        return jsonify({"error": f"#{channel} introuvable."}), 400

    config["channels"].remove(channel)
    save_config(config)

    if bot_instance and bot_loop and bot_running and not bot_loop.is_closed():
        asyncio.run_coroutine_threadsafe(bot_instance.part_channels([channel]), bot_loop)
        add_log(f"Quitté #{channel} en direct", "warning")
    else:
        add_log(f"#{channel} retiré de la liste", "info")

    return jsonify({"status": "removed", "channels": config["channels"]})


@app.route("/api/status")
def status():
    running = bot_running and bot_thread is not None and bot_thread.is_alive()
    connected = []
    if running and bot_instance:
        connected = [c.name for c in bot_instance.connected_channels]
    return jsonify({
        "running":   running,
        "channels":  config.get("channels", []),
        "connected": connected,
        "logs":      logs[-60:]
    })


@app.route("/api/config")
def get_config():
    safe = {**config, "token": ("●" * 24) if config["token"] else ""}
    return jsonify(safe)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    add_log(f"Serveur web lancé sur le port {port}", "info")
    app.run(host="0.0.0.0", port=port, debug=False)
