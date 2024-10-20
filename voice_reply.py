# 导入所需的模块
import json  # 用于处理JSON数据
import os  # 用于处理文件和目录操作
import threading  # 用于创建定时任务
import atexit  # 用于注册程序退出时的回调函数
from bridge.reply import Reply, ReplyType  # 导入回复相关的类
from bridge.context import ContextType  # 导入上下文类型
from plugins import register, Plugin, Event, EventContext, EventAction  # 导入插件相关的类和函数
from common.log import logger  # 导入日志记录器
import requests  # 用于发送HTTP请求
import re  # 用于正则表达式匹配

# 注册插件,提供插件的基本信息
@register(name="VoiceReply",
           desc="生成指定音色的语音回复", version="1.1", 
           author="claude3.5")
class VoiceReply(Plugin):
    def __init__(self):
        super().__init__()  # 调用父类的初始化方法
        try:
            # 获取插件目录路径
            self.plugin_dir = os.path.dirname(__file__)  # 获取当前文件所在的目录
            
            # 构建配置文件路径
            config_path = os.path.join(self.plugin_dir, "config.json")  # 拼接配置文件的完整路径
            if os.path.exists(config_path):  # 检查配置文件是否存在
                with open(config_path, "r", encoding="utf-8") as f:  # 打开并读取配置文件
                    config = json.load(f)  # 解析JSON配置
                    # 从配置中获取各项设置
                    self.api_key = config.get("api_key")  # 获取API密钥
                    self.base_url = config.get("base_url")  # 获取基础URL
                    self.max_chars = config.get("max_chars", 3000)  # 获取最大字符数，默认3000
                    self.model = config.get("model", "tts-1")  # 获取TTS模型名称，默认"tts-1"
                    self.voices = config.get("voices", {})  # 获取可用的音色列表
                    self.summary_model = config.get("summary_model", "gpt-4o-mini")  # 获取总结模型名称，默认"gpt-4o-mini"
            else:
                raise Exception("配置文件未找到")  # 如果配置文件不存在,抛出异常
            
            # 在插件目录下创建temp文件夹
            self.temp_dir = os.path.join(self.plugin_dir, "temp")  # 创建临时文件夹路径
            os.makedirs(self.temp_dir, exist_ok=True)  # 创建临时文件夹，如果已存在则不报错
            logger.info(f"[VoiceReply] 临时文件夹创建成功: {self.temp_dir}")  # 记录临时文件夹创建成功的日志

            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context  # 注册事件处理器
            logger.info("[VoiceReply] 插件初始化成功 🎉")  # 记录插件初始化成功的日志

            # 注册退出时的清理函数
            atexit.register(self.cleanup_all_temp_files)  # 注册程序退出时要执行的清理函数
        except Exception as e:
            logger.error(f"[VoiceReply] 插件初始化失败 😱: {e}")  # 记录插件初始化失败的错误日志

    def on_handle_context(self, e_context: EventContext):
        if e_context['context'].type != ContextType.TEXT:  # 检查消息类型是否为文本
            return

        content = e_context['context'].content.strip()  # 获取消息内容并去除前后的空格
        logger.debug(f"[VoiceReply] 收到消息: {content}")  # 记录收到的消息
        
        if content == "语音帮助":  # 如果用户请求帮助
            logger.info("[VoiceReply] 用户请求帮助信息")  # 记录用户请求帮助
            help_text = self.get_help_text()  # 获取帮助文本
            e_context["reply"] = Reply(ReplyType.TEXT, help_text)  # 设置回复为文本类型的帮助信息
            e_context.action = EventAction.BREAK_PASS  # 设置事件处理完成
            return

        if content.startswith("语音"):  # 检查是否是语音命令
            # 使用正则表达式匹配语音生成命令
            match = re.match(r'^语音\s+([\s\S]+?)(?:\s+([a-zA-Z]+))?$', content, re.DOTALL)
            if match:
                text = match.group(1).strip()  # 提取文本内容
                voice = match.group(2) or "alloy"  # 获取音色，如果没有指定则使用默认值"alloy"
            else:
                e_context["reply"] = Reply(ReplyType.ERROR, "语音命令格式错误，请检查后重试 😅")
                e_context.action = EventAction.BREAK_PASS
                return

            logger.debug(f"[VoiceReply] 处理的文本: '{text}', 音色: '{voice}'")  # 记录处理的文本和音色
            
            if voice not in self.voices:  # 检查音色是否有效
                logger.warning(f"[VoiceReply] 未知的音色: {voice}")  # 记录未知音色警告
                e_context["reply"] = Reply(ReplyType.ERROR, f"未知的音色: {voice} 😅\n可用音色: {', '.join(self.voices.keys())}")
                e_context.action = EventAction.BREAK_PASS
                return
            
            if len(text) > self.max_chars:  # 检查文本长度是否超过限制
                logger.warning(f"[VoiceReply] 文本超过最大长度: {len(text)} > {self.max_chars}")  # 记录文本超长警告
                e_context["reply"] = Reply(ReplyType.ERROR, f"文本超过最大长度限制 ({self.max_chars} 字符) 😓")
                e_context.action = EventAction.BREAK_PASS
                return
            
            try:
                logger.info(f"[VoiceReply] 开始生成语音: 文本='{text}', 音色='{voice}'")  # 记录开始生成语音
                file_name = self.generate_file_name(text)  # 生成文件名
                audio_file = self.generate_voice(text, voice, file_name)  # 生成语音文件
                logger.info(f"[VoiceReply] 语音生成成功: {audio_file}")  # 记录语音生成成功
                e_context["reply"] = Reply(ReplyType.VOICE, audio_file)  # 设置回复为语音类型
                
                # 设置延迟清理任务
                threading.Timer(300, self.delayed_file_cleanup, args=[audio_file]).start()  # 创建一个定时器，5分钟后清理文件
            except Exception as e:
                logger.error(f"[VoiceReply] 生成语音失败 😖: {e}")  # 记录语音生成失败
                e_context["reply"] = Reply(ReplyType.ERROR, "生成语音失败，请稍后再试 🙏")
            
            e_context.action = EventAction.BREAK_PASS  # 设置事件处理完成

    def generate_file_name(self, text):
        try:
            # 构建API请求URL和头部
            api_url = f"{self.base_url}/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            
            # 构建请求负载
            payload = {
                "model": self.summary_model,
                "messages": [
                    {"role": "system", "content": "你是一个专门生成文件名的AI助手。你的任务是为给定的文本生成一个简短、有意义的文件名。要求如下：\n1. 文件名必须严格控制在10个字符以内\n2. 不得包含任何特殊字符或空格\n3. 文件名应该反映文本的主要内容或主题\n4. 如果原文是中文，生成中文文件名；如果原文是英文，生成英文文件名\n5. 如果是中英混合，根据主要语言决定文件名语言"},
                    {"role": "user", "content": f"为以下文本生成一个文件名：\n{text}"}
                ],
                "functions": [{
                    "name": "generate_filename",
                    "description": "生成一个不超过10个字符的简短文件名",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": "生成的文件名，不包含扩展名，严格限制在10个字符以内，不包含特殊字符或空格。中文文本用中文文件名，英文文本用英文文件名。"
                            }
                        },
                        "required": ["filename"]
                    }
                }],
                "function_call": {"name": "generate_filename"}
            }

            logger.debug(f"[VoiceReply] 发送请求到 OpenAI API: {api_url}")
            # 发送POST请求到OpenAI API
            response = requests.post(api_url, headers=headers, json=payload)
            response.raise_for_status()  # 如果请求失败，抛出异常
            response_data = response.json()  # 解析JSON响应

            logger.debug(f"[VoiceReply] OpenAI API 响应: {response_data}")

            # 从API响应中提取文件名
            if 'choices' in response_data and len(response_data['choices']) > 0:
                choice = response_data['choices'][0]
                if 'function_call' in choice['message']:
                    function_call = choice['message']['function_call']
                    if function_call['name'] == "generate_filename":
                        file_name = json.loads(function_call['arguments'])["filename"]
                        file_name = file_name[:10]  # 再次确保不超过10个字符
                    else:
                        raise Exception("未找到有效的文件名生成结果")
                else:
                    # 如果没有 function_call，尝试从普通消息内容中提取文件名
                    file_name = choice['message']['content'].strip()[:10]
            else:
                raise Exception("API 响应中没有有效的选择")

            # 检查文件名是否已存在，如果存在则添加序号
            base_name = file_name
            counter = 1
            while os.path.exists(os.path.join(self.temp_dir, f"{file_name}.mp3")):
                file_name = f"{base_name[:8]}_{counter}"  # 确保添加序号后仍不超过10个字符
                counter += 1
            
            logger.info(f"[VoiceReply] 生成的文件名: {file_name}")
            return file_name
        except requests.RequestException as e:
            logger.error(f"[VoiceReply] API 请求失败: {str(e)}")
            return f"voice_{hash(text) % 1000000:06}"
        except Exception as e:
            logger.error(f"[VoiceReply] 生成文件名失败: {str(e)}")
            return f"voice_{hash(text) % 1000000:06}"

    def generate_voice(self, text, voice, file_name):
        url = f"{self.base_url}/v1/audio/speech"  # 构建API请求URL
        headers = {
            "Authorization": f"Bearer {self.api_key}",  # 设置认证头
            "Content-Type": "application/json"  # 设置内容类型
        }
        data = {
            "model": self.model,  # 设置使用的模型
            "input": text,  # 设置要转换的文本
            "voice": voice  # 设置使用的音色
        }
        try:
            logger.info(f"[VoiceReply] 发送API请求: URL={url}, 文本长度={len(text)}, 音色={voice}")  # 记录API请求信息
            response = requests.post(url, headers=headers, json=data)  # 发送POST请求
            response.raise_for_status()  # 检查响应状态，如果不是200则抛出异常
            
            file_path = os.path.join(self.temp_dir, f"{file_name} - {voice}.mp3")  # 构建完整的文件路径
            with open(file_path, "wb") as f:
                f.write(response.content)  # 将音频内容写入文件
            logger.info(f"[VoiceReply] 音频文件已保存: {file_path}")  # 记录文件保存成功
            return file_path  # 返回文件路径
        except requests.RequestException as e:
            logger.error(f"[VoiceReply] API请求失败 😭: {e}")  # 记录API请求失败
            raise Exception("生成语音失败")
        except Exception as e:
            logger.error(f"[VoiceReply] 保存音频文件失败 😖: {e}")  # 记录文件保存失败
            raise Exception("保存音频文件失败")

    def delayed_file_cleanup(self, file_path):
        try:
            if os.path.exists(file_path):  # 检查文件是否存在
                os.remove(file_path)  # 删除文件
                logger.info(f"[VoiceReply] 临时文件已清理: {file_path}")  # 记录文件清理成功
            else:
                logger.info(f"[VoiceReply] 临时文件已不存在: {file_path}")  # 记录文件已不存在
        except Exception as e:
            logger.error(f"[VoiceReply] 清理临时文件失败: {e}")  # 记录文件清理失败

    def cleanup_all_temp_files(self):
        try:
            for file in os.listdir(self.temp_dir):  # 遍历临时目录中的所有文件
                if file.endswith(".mp3"):  # 检查是否是MP3文件
                    file_path = os.path.join(self.temp_dir, file)  # 构建完整的文件路径
                    os.remove(file_path)  # 删除文件
                    logger.info(f"[VoiceReply] 清理临时文件: {file_path}")  # 记录文件清理成功
        except Exception as e:
            logger.error(f"[VoiceReply] 清理所有临时文件失败: {e}")  # 记录清理所有文件失败

    def get_help_text(self, **kwargs):
        help_text = "🎙️ 语音回复插件使用说明：\n\n"  # 初始化帮助文本
        help_text += "生成语音：语音 <文本内容> [音色]\n"  # 添加使用说明
        help_text += "例如：语音 你好世界\n"  # 添加示例
        help_text += "或者：语音 你好世界 alloy\n\n"  # 添加带音色的示例
        help_text += "可用音色列表：\n"  # 添加音色列表标题
        for voice, desc in self.voices.items():
            help_text += f"- {voice}: {desc}\n"  # 添加每种音色的描述
        help_text += f"\n💡 提示：默认音色为 alloy，使用的TTS模型为 {self.model}"  # 添加默认设置提示
        help_text += f"\n💡 文件名生成使用的模型为 {self.summary_model}"  # 添加文件名生成模型信息
        help_text += f"\n最大文本长度限制为 {self.max_chars} 字符"  # 添加字数限制提示
        return help_text  # 返回帮助文本
