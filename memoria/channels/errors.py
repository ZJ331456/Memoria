"""Sanitized channel errors; no raw provider descriptions or request URLs."""
class ChannelError(Exception):
    def __init__(self,code):
        self.code=str(code)
        super().__init__('channel_error_'+self.code)
