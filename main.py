from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import uvicorn
import os
from dotenv import load_dotenv
from typing import Optional, List
import json
import aiofiles
import pandas as pd
from datetime import datetime
import uuid
from redis_helper import redis_helper
from openai_helper import openai_helper

# 加载环境变量
load_dotenv()

app = FastAPI(title="AI题目生成系统")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置模板
templates = Jinja2Templates(directory="templates")

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 创建必要的目录
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("output", exist_ok=True)

class GenerationParams(BaseModel):
    """生成参数模型"""
    count: int = Field(..., ge=1, le=500, description="生成题目数量")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="温度参数")
    top_p: float = Field(0.9, ge=0.0, le=1.0, description="核采样参数")
    presence_penalty: float = Field(0.0, ge=-2.0, le=2.0, description="主题重复惩罚")
    frequency_penalty: float = Field(0.0, ge=-2.0, le=2.0, description="词语重复惩罚")
    max_tokens: int = Field(1000, ge=100, le=4000, description="最大token数")
    concurrent_tasks: int = Field(5, ge=1, le=20, description="并发任务数")
    progress_interval: int = Field(1000, ge=500, le=5000, description="进度检查间隔(毫秒)")
    task_expire: int = Field(3600, ge=600, le=7200, description="任务过期时间(秒)")
    system_prompt: str = Field(..., min_length=1, description="系统提示词")
    user_prompt: str = Field(..., min_length=1, description="用户提示词")
    json_fields: List[dict] = Field(..., description="JSON字段定义")

async def generate_questions_task(task_id: str, params: dict):
    """后台任务：生成题目"""
    try:
        print(f"开始生成题目，任务ID: {task_id}")
        print(f"参数: {json.dumps(params, ensure_ascii=False, indent=2)}")
        
        # 初始化任务状态
        await redis_helper.set_task_status(task_id, {
            "status": "running",
            "progress": {"current": 0, "total": params["count"], "percentage": 0},
            "start_time": datetime.now().isoformat(),
            "task_expire": params.get("task_expire", 3600)  # 添加过期时间
        })

        # 定义进度回调
        async def progress_callback(current: int, total: int):
            await redis_helper.update_task_progress(task_id, current, total)

        # 生成题目
        questions = await openai_helper.generate_questions_batch(params, progress_callback)
        print(f"生成的题目数量: {len(questions)}")
        print(f"第一道题目示例: {json.dumps(questions[0] if questions else {}, ensure_ascii=False, indent=2)}")

        # 获取用户定义的字段顺序
        json_fields = params.get('json_fields', [])
        print(f"用户定义的字段: {json.dumps(json_fields, ensure_ascii=False, indent=2)}")
        
        field_names = [field['name'] for field in json_fields]
        field_descriptions = {field['name']: field['description'] for field in json_fields}
        
        print(f"字段名列表: {field_names}")
        print(f"字段描述映射: {json.dumps(field_descriptions, ensure_ascii=False, indent=2)}")
        
        # 创建Excel文件，按用户定义的字段顺序排列
        df = pd.DataFrame(questions)
        print(f"DataFrame原始列: {df.columns.tolist()}")
        
        # 确保所有字段都存在
        for field in field_names:
            if field not in df.columns:
                print(f"添加缺失字段: {field}")
                df[field] = ''
        
        # 重新排序列
        df = df[field_names]
        print(f"重排序后的列: {df.columns.tolist()}")
        
        # 重命名列为字段描述
        df = df.rename(columns=field_descriptions)
        print(f"重命名后的列: {df.columns.tolist()}")
        
        output_file = f"output/questions_{task_id}.xlsx"
        print(f"输出文件路径: {output_file}")
        
        # 设置Excel列的宽度
        writer = pd.ExcelWriter(output_file, engine='openpyxl')
        df.to_excel(writer, index=False, sheet_name='题目')
        
        # 获取工作表
        worksheet = writer.sheets['题目']
        
        # 设置所有列的宽度为50
        for column in worksheet.columns:
            worksheet.column_dimensions[column[0].column_letter].width = 50
        
        # 保存文件
        writer.close()
        print(f"Excel文件已保存: {output_file}")

        # 更新任务状态为完成
        await redis_helper.set_task_status(task_id, {
            "status": "completed",
            "progress": {"current": params["count"], "total": params["count"], "percentage": 100},
            "output_file": output_file,
            "completion_time": datetime.now().isoformat(),
            "task_expire": params.get("task_expire", 3600)  # 添加过期时间
        })
        print(f"任务完成: {task_id}")

    except Exception as e:
        print(f"任务出错: {str(e)}")
        # 更新任务状态为错误
        await redis_helper.set_task_error(task_id, str(e))

@app.post("/generate")
async def generate_questions(params: GenerationParams, background_tasks: BackgroundTasks):
    """开始生成题目"""
    task_id = str(uuid.uuid4())
    background_tasks.add_task(generate_questions_task, task_id, params.dict())
    return {"task_id": task_id}

@app.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态"""
    status = await redis_helper.get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return status

@app.get("/download/{task_id}")
async def download_result(task_id: str):
    """下载生成的Excel文件"""
    status = await redis_helper.get_task_status(task_id)
    if not status or status.get("status") != "completed":
        raise HTTPException(status_code=404, detail="Result not found or task not completed")
    
    output_file = status.get("output_file")
    if not os.path.exists(output_file):
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        output_file,
        filename=f"questions_{task_id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.get("/")
async def read_root(request: Request):
    """返回主页"""
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        reload=True
    ) 