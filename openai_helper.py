from openai import OpenAI
import os
from dotenv import load_dotenv
from typing import Dict, Any, List
import json
import asyncio
from datetime import datetime

load_dotenv()

class OpenAIHelper:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_API_BASE")
        )
        self.model = "gpt-4o"  # 固定使用 gpt-4o
        self._semaphore = None  # 动态创建信号量

    @property
    def semaphore(self):
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(10)  # 默认值
        return self._semaphore

    def set_concurrent_tasks(self, count: int):
        """设置并发任务数"""
        self._semaphore = asyncio.Semaphore(count)

    async def generate_question(self, params: Dict[str, Any], index: int) -> Dict[str, Any]:
        """生成单个题目"""
        async with self.semaphore:  # 使用信号量控制并发
            try:
                # 从params中获取用户定义的JSON字段
                json_fields = params.get('json_fields', [])
                
                # 构建动态的JSON格式示例
                example_json = {
                    field['name']: f"{field['description']}的内容"
                    for field in json_fields
                }
                
                # 添加格式要求到系统提示词
                format_requirement = f"""
                请严格按照以下JSON格式返回题目：
                {json.dumps(example_json, ensure_ascii=False, indent=4)}

                注意：
                1. 必须是合法的JSON格式
                2. 必须包含上述所有字段，且字段名必须完全一致
                3. 不要添加其他字段
                4. 不要包含任何其他文字说明
                5. 不要添加markdown标记
                6. 每个字段的值必须是字符串，不能是对象或数组
                """
                
                system_prompt = f"{format_requirement}\n\n{params['system_prompt']}"
                
                response = await asyncio.to_thread(
                    self.client.chat.completions.create,
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": params["user_prompt"]}
                    ],
                    temperature=params["temperature"],
                    top_p=params["top_p"],
                    presence_penalty=params["presence_penalty"],
                    frequency_penalty=params["frequency_penalty"],
                    max_tokens=params["max_tokens"]
                )
                
                # 解析返回的JSON
                try:
                    content = response.choices[0].message.content.strip()
                    
                    # 移除可能的markdown标记
                    if content.startswith("```"):
                        content = content.split("\n", 1)[1]  # 移除第一行
                    if content.endswith("```"):
                        content = content.rsplit("\n", 1)[0]  # 移除最后一行
                    content = content.strip()
                    
                    # 尝试解析JSON
                    result = json.loads(content)
                    
                    # 验证所有必要字段
                    required_fields = [field['name'] for field in json_fields]
                    missing_fields = []
                    
                    for field in required_fields:
                        if field not in result:
                            missing_fields.append(field)
                    
                    if missing_fields:
                        error_result = {field: "" for field in required_fields}
                        error_result.update({
                            "question": f"[生成失败] 第{index+1}题",
                            "analysis": f"错误：返回的JSON缺少必要字段 {', '.join(missing_fields)}\n原始内容：{content}"
                        })
                        return error_result
                    
                    # 确保结果包含所有必要字段
                    for field in required_fields:
                        if field not in result:
                            result[field] = ""
                    
                    return result
                    
                except json.JSONDecodeError as e:
                    error_result = {field['name']: "" for field in json_fields}
                    error_result.update({
                        "question": f"[生成失败] 第{index+1}题",
                        "analysis": f"错误：AI返回的内容不是有效的JSON格式\n原始内容：{content}"
                    })
                    return error_result
                    
            except Exception as e:
                error_result = {field['name']: "" for field in json_fields}
                error_result.update({
                    "question": f"[生成失败] 第{index+1}题",
                    "analysis": f"错误：{str(e)}"
                })
                return error_result

    async def generate_questions_batch(self, params: Dict[str, Any], progress_callback) -> List[Dict[str, Any]]:
        """批量生成题目"""
        total = params["count"]
        # 设置并发数
        self.set_concurrent_tasks(params.get("concurrent_tasks", 5))
        tasks = []
        
        # 创建所有生成任务
        for i in range(total):
            task = asyncio.create_task(self.generate_question(params, i))
            tasks.append(task)
        
        # 使用列表存储结果
        questions = []
        completed = 0
        
        try:
            for coro in asyncio.as_completed(tasks):
                try:
                    result = await coro
                    if result is None:
                        # 如果结果是None，创建一个错误结果
                        error_result = {field['name']: "" for field in params['json_fields']}
                        error_result.update({
                            "question": f"[生成失败] 第{completed+1}题",
                            "analysis": "生成过程中发生错误"
                        })
                        result = error_result
                    
                    # 确保结果包含所有必要字段
                    field_names = [field['name'] for field in params['json_fields']]
                    for field in field_names:
                        if field not in result:
                            result[field] = ""
                    
                    # 直接添加到列表末尾，不需要预分配空间
                    questions.append(result)
                    completed += 1
                    await progress_callback(completed, total)
                    
                except Exception as e:
                    # 处理单个任务的异常
                    error_result = {field['name']: "" for field in params['json_fields']}
                    error_result.update({
                        "question": f"[生成失败] 第{completed+1}题",
                        "analysis": f"错误：{str(e)}"
                    })
                    questions.append(error_result)
                    completed += 1
                    await progress_callback(completed, total)
        
        except Exception as e:
            # 处理整体任务的异常
            raise Exception(f"批量生成题目时发生错误: {str(e)}")
        
        # 确保返回的列表长度正确
        if len(questions) != total:
            raise Exception(f"生成的题目数量不正确: 预期 {total}, 实际 {len(questions)}")
        
        return questions

openai_helper = OpenAIHelper() 