"""Diagnostic bot: echoes everything back. Useful to verify the iLink
pairing works before involving Claude. Long-polls ilinkai.weixin.qq.com;
no inbound server needed.
"""

from weixin_ilink import WeixinBot

from .config import require_creds


def main() -> None:
    bot = WeixinBot(credentials_file=require_creds())

    @bot.on_text
    def echo_text(msg):
        print(f"<- text from {msg.from_user}: {msg.text}", flush=True)
        msg.reply_text(f"echo: {msg.text}")

    @bot.on_voice
    def echo_voice(msg):
        print(f"<- voice from {msg.from_user}, transcript: {msg.text!r}", flush=True)
        msg.reply_text(f"heard ({msg.voice_duration_ms} ms): {msg.text or '<no transcript>'}")

    @bot.on_image
    def echo_image(msg):
        print(f"<- image from {msg.from_user}", flush=True)
        msg.reply_text("got your image")

    @bot.on_file
    def echo_file(msg):
        print(f"<- file from {msg.from_user}: {msg.file_name}", flush=True)
        msg.reply_text(f"got file: {msg.file_name}")

    @bot.on_video
    def echo_video(msg):
        print(f"<- video from {msg.from_user}", flush=True)
        msg.reply_text("got your video")

    print(f"echo bot running as {bot.account_id}, waiting for messages...", flush=True)
    bot.run()


if __name__ == "__main__":
    main()
