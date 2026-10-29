from django.core.mail.backends.console import EmailBackend as ConsoleEmailBackend


class SafeConsoleEmailBackend(ConsoleEmailBackend):
    """Dev console backend that survives non-UTF-8 terminals (Windows cp1252 can't print "→" or Bangla)."""

    def write_message(self, message):
        try:
            super().write_message(message)
        except UnicodeEncodeError:
            text = message.message().as_bytes().decode("utf-8", errors="replace")
            encoding = getattr(self.stream, "encoding", None) or "ascii"
            self.stream.write(text.encode(encoding, errors="backslashreplace").decode(encoding))
            self.stream.write("\n" + "-" * 79 + "\n")
