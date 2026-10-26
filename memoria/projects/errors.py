"""Business-layer error carrying an HTTP status, mirroring LibraryError."""


class ProjectError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail
