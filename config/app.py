from dotenv import load_dotenv
import os

class AWSS3Config:
    def __init__(self, bucket: str, region: str, access_key_id: str, secret_access_key: str ):
        self.bucket = bucket
        self.region = region
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key

class Config:
    def __init__(self):
        load_dotenv()
            
        self.awss3 = AWSS3Config(
            bucket=os.getenv('AWS_BUCKET_NAME'),
            region=os.getenv('AWS_REGION'),
            access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        )