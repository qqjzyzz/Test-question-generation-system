import redis
import json
from typing import Dict, Any, Optional
import os
from dotenv import load_dotenv

load_dotenv()

class RedisHelper:
    def __init__(self):
        self.redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True
        )

    async def set_task_status(self, task_id: str, status: Dict[str, Any], expire: Optional[int] = None) -> None:
        """设置任务状态"""
        # 如果status中包含task_expire，使用它作为过期时间
        if isinstance(status, dict) and "task_expire" in status:
            expire = status["task_expire"]
        elif expire is None:
            expire = 3600  # 默认1小时过期
            
        self.redis_client.set(
            f"task:{task_id}",
            json.dumps(status),
            ex=expire
        )

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        status = self.redis_client.get(f"task:{task_id}")
        return json.loads(status) if status else None

    async def update_task_progress(self, task_id: str, current: int, total: int) -> None:
        """更新任务进度"""
        status = await self.get_task_status(task_id)
        if status:
            status["progress"] = {
                "current": current,
                "total": total,
                "percentage": round(current / total * 100, 2)
            }
            await self.set_task_status(task_id, status)

    async def set_task_error(self, task_id: str, error_message: str) -> None:
        """设置任务错误状态"""
        status = await self.get_task_status(task_id)
        if status:
            status["status"] = "error"
            status["error"] = error_message
            await self.set_task_status(task_id, status)

    async def delete_task(self, task_id: str) -> None:
        """删除任务状态"""
        self.redis_client.delete(f"task:{task_id}")

redis_helper = RedisHelper() 