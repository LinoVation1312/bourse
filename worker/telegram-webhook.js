// Cloudflare Worker (gratuit) : relaie chaque message Telegram vers GitHub Actions pour une réponse en ≈ 30 s,
// au lieu d'attendre le prochain passage du cron. Variables à définir dans le Worker (Settings → Variables) :
//   TG_CHAT         ton chat_id Telegram (les autres expéditeurs sont ignorés)
//   WEBHOOK_SECRET  chaîne aléatoire, la même que dans setWebhook (secret_token)
//   GH_REPO         « tonpseudo/bourse »
//   GH_TOKEN        jeton GitHub (fine-grained, ce dépôt seulement, permission « Contents: Read and write »)
export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const update = await request.json();
    const msg = update.message;
    if (!msg || !msg.text || String(msg.chat?.id) !== String(env.TG_CHAT)) return new Response("ignored");

    const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "bourse-bot",
      },
      body: JSON.stringify({
        event_type: "telegram",
        client_payload: { update: { update_id: update.update_id, message: { chat: { id: msg.chat.id }, text: msg.text } } },
      }),
    });
    // 502 si GitHub refuse : Telegram réessaiera le même message.
    return new Response(r.ok ? "ok" : "dispatch failed", { status: r.ok ? 200 : 502 });
  },
};
