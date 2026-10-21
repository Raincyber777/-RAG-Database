import alibabacloud_oss_v2 as oss
from fastapi import APIRouter
from datetime import timedelta
import os

router = APIRouter()

credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()

cfg = oss.config.load_default()
cfg.credentials_provider = credentials_provider

cfg.region = 'cn-beijing'

client = oss.Client(cfg)

OSS_ENDPOINT = os.getenv("OSS_ENDPOINT","oss-cn-beijing.aliyuncs.com")
OSS_BUCKET = os.getenv("OSS_BUCKET")

@router.get("/oss/presign")
def chat_endpoint(filename:str):
    content_type_map={
        "jpg":"image/jpeg",
        "png":"image/png",
        "gif":"image/gif",
        "jpeg":"image/jpeg",
        "webp":"image/webp",
    }
    exit = filename.split(".")[-1].lower() if "." in filename else "jpg"
    content_type = content_type_map.get(exit, "application/octet-stream")

    pre_result = client.presign(oss.PutObjectRequest(
        bucket=OSS_BUCKET,
        key=filename,
        content_type=content_type,
    ),expires=timedelta(seconds=3600))

    return {
        "uploadUrl": pre_result.url.strip('"'),
        "contentType": content_type,
        "accessUrl": f"https://{OSS_BUCKET}.{OSS_ENDPOINT}/{filename}"
    }

