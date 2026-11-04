"""Telegram Bot API: post listings to the operator's own channel.

The bot must be an admin of the channel. Telegram bots cannot post in other
people's groups unless an admin there adds them, so groups you do not run stay
on the hand-posting list.
"""

import requests

API = "https://api.telegram.org/bot{token}/{method}"
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, connection, session=None):
        if not connection.has_secret:
            raise TelegramError("Add the bot token to this connection first.")
        self.connection = connection
        self.token = connection.secret
        self.chat_id = connection.account_id
        self.http = session or requests.Session()

    def _call(self, method, data=None, files=None):
        resp = self.http.post(API.format(token=self.token, method=method), data=data, files=files, timeout=60)
        try:
            body = resp.json()
        except ValueError as exc:
            raise TelegramError(f"Telegram {method} returned {resp.status_code}") from exc
        if not body.get("ok"):
            raise TelegramError(f"Telegram {method}: {body.get('description', resp.status_code)}")
        return body["result"]

    def me(self):
        return self._call("getMe")

    def post(self, text, photo_files=()):
        """Post text with up to 10 photos. Returns the first message ID."""
        if not self.chat_id:
            raise TelegramError("Set the channel (for example @oceanway_rooms) on this connection.")
        photos = list(photo_files)[:10]
        if not photos:
            return self._call("sendMessage", data={"chat_id": self.chat_id, "text": text[:MESSAGE_LIMIT]})["message_id"]
        caption, rest = text[:CAPTION_LIMIT], text[CAPTION_LIMIT:]
        if len(photos) == 1:
            with photos[0].open("rb") as fh:
                result = self._call(
                    "sendPhoto", data={"chat_id": self.chat_id, "caption": caption}, files={"photo": fh.read()}
                )
            first_id = result["message_id"]
        else:
            import json

            media, files = [], {}
            for i, f in enumerate(photos):
                with f.open("rb") as fh:
                    files[f"p{i}"] = fh.read()
                item = {"type": "photo", "media": f"attach://p{i}"}
                if i == 0:
                    item["caption"] = caption
                media.append(item)
            result = self._call(
                "sendMediaGroup", data={"chat_id": self.chat_id, "media": json.dumps(media)}, files=files
            )
            first_id = result[0]["message_id"]
        if rest:
            self._call("sendMessage", data={"chat_id": self.chat_id, "text": rest[:MESSAGE_LIMIT]})
        return first_id

    def message_url(self, message_id):
        if self.chat_id.startswith("@"):
            return f"https://t.me/{self.chat_id[1:]}/{message_id}"
        return ""
