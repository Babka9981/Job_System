import os
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible

@deconstructible
class TemporaryContentStorage(FileSystemStorage):
    @property
    def base_location(self):
        return settings.TEMPORARY_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

temporary_content_storage = TemporaryContentStorage()
