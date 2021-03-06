class NotFoundError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class ConfigureError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class ModelError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class InvalidNumberOfCharacters(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message

