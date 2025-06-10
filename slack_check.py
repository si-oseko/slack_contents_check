headers = {
    "Authorization": f"Bearer {SLACK_TOKEN}",
    "Content-Type":  "application/json"
}

# ── Slack API 共通（レートリミット対応） ──
def slack_request(method, url, params=None, json_payload=None, max_retries=5):
    for i in range(max_retries):
        res  = requests.request(method, url, headers=headers,
                                params=params, json=json_payload)
        data = res.json()
        if data.get("ok"):
            return data
        if data.get("error") == "ratelimited":
            wait = int(res.headers.get("Retry-After", 30))
            print(f"[rate-limited] {wait}s 待機 ({i+1}/{max_retries})")
            time.sleep(wait)
            continue
        print(f"[Slack error] {data.get('error')}")
        return data
    raise RuntimeError("Slack API: retry exceeded")

# ── ① ユーザー辞書 ──
def get_user_dict():
    data = slack_request("GET", "https://slack.com/api/users.list")
    d = {}
    for m in data.get("members", []):
        uid = m["id"]
        p   = m.get("profile", {})
        d[uid] = p.get("display_name") or p.get("real_name") or uid
    return d

# ── ② 過去24時間分メッセージ（ページネーション付き） ──
def get_messages_24h(channel_id):
    now_ts, oldest_ts = int(time.time()), int(time.time() - 24*3600)
    msgs, cursor = [], None
    while True:
        params = {
            "channel": channel_id, "oldest": str(oldest_ts),
            "latest": str(now_ts), "inclusive": True, "limit": 1000
        }
        if cursor: params["cursor"] = cursor
        data  = slack_request("GET", "https://slack.com/api/conversations.history",
                              params=params)
        msgs += data.get("messages", [])
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor: break
        time.sleep(1)
    return msgs

# ── ③ GPT 判定関数（共通） ──
def ask_gpt(prompt):
    try:
        r = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role":"system","content":"You are a strict moderator."},
                      {"role":"user","content":prompt}],
            temperature=0)
        return r.choices[0].message.content.strip().lower().startswith("はい")
    except Exception as e:
        print("OpenAI error:", e)
        return False

def is_abuse(name, text):
    p = f"次のSlackメッセージが誹謗中傷か判定。「はい」or「いいえ」\n発言者:{name}\n内容:{text}"
    return ask_gpt(p)

def is_praise(name, text):
    p = f"次のSlackメッセージが感謝または称賛表現か判定。「はい」or「いいえ」\n発言者:{name}\n内容:{text}"
    return ask_gpt(p)

# ── ④ Slack 投稿 ──
def post(channel, text):
    slack_request("POST", "https://slack.com/api/chat.postMessage",
                  json_payload={"channel": channel, "text": text})

# ── ⑤ メイン処理 ──
def main():
    users = get_user_dict()
    msgs  = get_messages_24h(SOURCE_CHANNEL_ID)

    print(f"ユーザー数: {len(users)} | メッセージ数: {len(msgs)}")
    print("=== 取得メッセージ ===")
    for m in msgs:
        ts = datetime.fromtimestamp(float(m["ts"])).strftime("%Y-%m-%d %H:%M:%S")
        name = users.get(m.get("user",""),"(不明)")
        print(f"{ts} | {name} | {m.get('text','')}")

    abuse_list   = []
    praise_count = {}          # {user: 件数}

    for m in reversed(msgs):
        uid  = m.get("user"); name = users.get(uid, "(不明)")
        text = m.get("text","");  time.sleep(1.2)  # OpenAI レート制限

        if text and is_abuse(name, text):
            abuse_list.append((name, text))
        elif text and is_praise(name, text):
            praise_count[name] = praise_count.get(name, 0) + 1

    # 誹謗中傷：個別投稿
    for n, t in abuse_list:
        post(REPORT_CHANNEL_ID, f"⚠️ *誹謗中傷検出*\n発言者: {n}\n内容: {t}")

    # 感謝/称賛：ユーザー別集計のみ投稿
    if praise_count:
        lines = [f"{u}: {c} 件" for u, c in praise_count.items()]
        body  = "🎉 *感謝/称賛メッセージ集計 (24h)*\n" + "\n".join(lines)
        post(REPORT_CHANNEL_ID, body)

    print("処理完了")

if __name__ == "__main__":
    main()