from io import IOBase


class Adapter:
    def store(self, data: IOBase):
        """
    s3.upload_fileobj(BytesIO(atomfeed), bucket, key)
    """
        pass

    def set_permission(self):
        """
    s3.put_object_acl(ACL="public-read", Bucket=bucket, Key=key)
    """
        pass

    def info(self):
        """
    s3.head_object(Bucket=bucket, Key=key)
    """
        pass
