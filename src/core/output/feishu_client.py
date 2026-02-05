import os
import json
from urllib import request
from urllib.error import URLError, HTTPError
from loguru import logger

class FeishuClient:
    def __init__(self):
        self.app_token = os.getenv("FEISHU_APP_TOKEN", "")
        self.table_id = os.getenv("FEISHU_TABLE_ID", "")
        self.tenant_access_token = os.getenv("FEISHU_TENANT_TOKEN", "")

    def push_records(self, records_json: dict) -> bool:
        """
        Push records to Feishu Bitable via Open API.
        records_json should match API expected format.
        """
        if not (self.app_token and self.table_id and self.tenant_access_token):
            logger.warning("Feishu credentials not configured. Skipping push.")
            return False
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records"
        data = json.dumps(records_json).encode("utf-8")
        req = request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.tenant_access_token}")
        try:
            with request.urlopen(req) as resp:
                status = resp.status
                if 200 <= status < 300:
                    logger.info("Feishu push success")
                    return True
                else:
                    logger.error(f"Feishu push failed with status {status}")
                    return False
        except (URLError, HTTPError) as e:
            logger.error(f"Feishu push error: {e}")
            return False
