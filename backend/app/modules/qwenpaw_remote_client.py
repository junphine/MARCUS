"""Small authenticated HTTP/SSE client for QwenPaw Agent gateway."""
import os
import re
import base64
from pathlib import Path
from typing import List, Dict, Tuple
import requests
import json
import uuid
from datetime import datetime

image_mime_mapping = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".tiff": "image/tiff",
    ".ico": "image/x-icon",
}

# ---------------------------------------------------------------------------
# 超长 Markdown 处理参数
# ---------------------------------------------------------------------------
# 文件超过该大小（字节）时，改用「大纲导航 + 按需读取」策略，避免一次性塞入上下文
LARGE_MD_SIZE_THRESHOLD = 1_000_000
# 单个章节内容超过该字符数时，进一步按段落切块
MAX_SECTION_CHARS = 20_000
# 单轮读取的章节内容字符预算（防止单次请求上下文过大）
MAX_SECTION_CHARS_PER_ROUND = 40_000
# 大纲导航最多迭代轮数（防止模型反复请求陷入死循环）
MAX_OUTLINE_ROUNDS = 6

# 匹配 Markdown 标题行：`# 标题` ~ `###### 标题`
_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$')


def is_image_by_ext(file_path: Path):
    ext = file_path.suffix.lower()
    return ext in image_mime_mapping

def guess_image_mime(file_path: Path) -> str:
    """根据文件扩展名推断图片的 MIME 类型"""
    return image_mime_mapping.get(file_path.suffix.lower(), "image/jpeg")

class QwenPawRemoteClient:
    """通用Http连接器"""

    def __init__(
            self,
            base_url: str = "",
            agent_id: str = "default",
            skill_name: str = "",
            username: str = None,
            password: str = None,
            file_extensions: list = []
    ):
        """
        初始化生成器

        Args:
            base_url: QwenPaw 服务地址
            agent_id: Agent ID
            skill_name: 要调用的技能名称
            username: 用户名（如果需要认证）
            password: 密码（如果需要认证）
            file_extensions: 要处理的文件扩展名列表（如 ['.md', '.txt', '.png']），None 表示处理所有文件
        """
        self.base_url = base_url or os.environ.get("QWENPAW_BASE_URL","http://localhost:8088")
        self.api_url = f"{self.base_url}/api/console/chat"
        self.login_url = f"{self.base_url}/api/auth/login"
        self.agent_id = agent_id
        self.skill_name = skill_name
        self.username = username
        self.password = password
        self.auth_token = None
        self.file_extensions = file_extensions or ["md", "json", "txt", "csv", "xml", "html", "yml","jpg","png","svg","gif","webp"]

        # 自动登录
        if username and password:
            self._login()

    def _login(self) -> bool:
        """登录获取认证令牌"""
        try:
            response = requests.post(
                self.login_url,
                json={"username": self.username, "password": self.password},
                timeout=30
            )

            if response.status_code == 200:
                self.auth_token = response.json().get("token")
                print("+ 登录成功")
                return True
            else:
                print(f"x 登录失败：HTTP {response.status_code}")
                return False

        except Exception as e:
            print(f"x 登录异常：{e}")
            return False

    def version(self):
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Id": self.agent_id
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        with requests.get(
                f"{self.base_url}/api/version",
                headers=headers,
                timeout=6
        ) as response:
            if response.status_code != 200:
                return response.status_code, response.text
            data = json.loads(response.text)
            return 200, data['version']

    def scan_files(self, directory: str, recursive: bool = True) -> List[Path]:
        """
        扫描指定目录下的所有文件

        Args:
            directory: 要扫描的目录路径
            recursive: 是否递归扫描子目录

        Returns:
            List[Path]: 文件路径列表
        """
        dir_path = Path(directory)

        if not dir_path.exists():
            raise FileNotFoundError(f"目录不存在：{directory}")

        if not dir_path.is_dir():
            return [dir_path]
            #raise NotADirectoryError(f"不是目录：{directory}")

        # 如果指定了扩展名，则只扫描那些扩展名的文件
        if self.file_extensions:
            files = []
            for ext in self.file_extensions:
                pattern = f"**/*{ext}" if recursive else f"*{ext}"
                files.extend(dir_path.glob(pattern))
        else:
            # 否则扫描所有文件
            pattern = "**/*" if recursive else "*"
            files = list(dir_path.glob(pattern))

        # 过滤掉以 . 开头的隐藏文件和目录
        files = [f for f in files if f.is_file() and not f.name.startswith('.')]

        return sorted(files)

    def read_file(self, file_path: Path, max_length: int = 50000) -> str:
        """
        读取文件内容

        Args:
            file_path: 文件路径
            max_length: 最大读取字符数（避免超出 token 限制）

        Returns:
            str: 文件内容
        """
        try:
            # 尝试 UTF-8 编码
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 如果内容过长，截取前 max_length 个字符
            if len(content) > max_length:
                print(f"  ! 文件内容过长 ({len(content)} 字符)，截取前 {max_length} 字符")
                content = content[:max_length] + "\n\n[内容过长，已截断...]"

            return content

        except UnicodeDecodeError:
            # 尝试其他编码
            try:
                with open(file_path, 'r', encoding='gbk') as f:
                    content = f.read()
                if len(content) > max_length:
                    content = content[:max_length] + "\n\n[内容过长，已截断...]"
                return content
            except Exception as e:
                print(f"  x 读取文件失败（编码问题）: {e}")
                return ""
        except Exception as e:
            print(f"  x 读取文件失败：{e}")
            return ""

    def _post_chat(self,
            messages: List[Dict],
            workspace: str | None = None,
            session_id: str = None
    ) -> Dict:
        """
        发送完整的对话消息列表（每条消息含 role 与 content 块）并流式返回结果。

        与 prompt() 的区别：messages 可为多轮对话（system/user/assistant 交替），
        用于「大纲导航 + 按需读取」这类需要多轮交互的场景。

        Args:
            messages: 消息列表，每项形如 {"role": "...", "content": [块, ...]}
            workspace: 工作路径
            session_id: 会话 id
        Returns:
            Dict: 包含生成结果（content/success/error）
        """
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Id": self.agent_id
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        data = {
            "input": messages,
            "session_id": session_id,
            "user_id": self.username or "guest",
            "channel": "console",
            "request_context": {
                "session_project_dir": workspace
            }
        }

        result = {
            "content": "",
            "success": False,
            "error": None
        }

        try:
            with requests.post(self.api_url, headers=headers, json=data, timeout=600) as response:
                if response.status_code != 200:
                    result["error"] = f"HTTP {response.status_code}: {response.text}"
                    return result

                for line in response.iter_lines():
                    if line:
                        line = line.decode('utf-8')
                        if line.startswith('data: '):
                            event_data = json.loads(line[6:])

                            # 检查错误
                            if event_data.get('error'):
                                error_msg = event_data['error'].get('message', '未知错误')
                                result["error"] = error_msg
                                print(f"\n x 错误：{error_msg}")
                                break

                            # 处理输出
                            if event_data.get('output'):
                                for item in event_data['output']:
                                    if item.get('role') == 'assistant':
                                        for content_item in item.get('content', []):
                                            if content_item.get('type') == 'text':
                                                text = content_item.get('text', '')
                                                result["content"] += text
                                                print(text, end='', flush=True)

            result["success"] = result["error"] is None

        except requests.exceptions.Timeout:
            result["error"] = "请求超时（600 秒）"
            print(f"\n x 请求超时")
        except Exception as e:
            result["error"] = str(e)
            print(f"\n x 请求异常：{e}")

        return result

    def prompt(self,
            content: List[Dict],
            workspace: str | None = None,
            system_prompt: List[Dict] | None = None,
            session_id: str = None
    ) -> Dict:
        """
        基于内容生成输出

        Args:
            content: 文件内容
            workspace: 工作路径
            system_prompt： 系统提示词
            session_id： 会话id
        Returns:
            Dict: 包含生成结果
        """
        intput = []
        if system_prompt:
            intput.append({"role": "system", "content": system_prompt})
        intput.append({"role": "user", "content": content})

        return self._post_chat(intput, workspace=workspace, session_id=session_id)

    def process_directory(
            self,
            prompt: List[Dict],
            directory: List[str]|str,
            output_dir: str = "",
            recursive: bool = True,
            only_path: bool = False,
            session_id: str = ""
    ) -> Dict:
        """
        处理整个目录的文件

        Args:
            prompt: 用户提示词
            directory: 输入目录，等同于workspace
            output_dir: 输出目录
            recursive: 是否递归扫描子目录
            only_path: 是否只发送文件路径给agent的skill（如果skill支持文件路径作为输入），否则发送文件内容到agent

        Returns:
            Dict: 处理结果统计
        """

        # 扫描文件
        try:
            files = []
            if type(directory)==type([]):
                for item in directory:
                    files += self.scan_files(item, recursive)
            else:
                files = self.scan_files(directory, recursive)
        except Exception as e:
            print(f"x 扫描失败：{e}")
            return {"success": False, "error": str(e)}

        if not files:
            print("x 未找到任何文件")
            return {"success": False, "error": "未找到文件"}

        print(f"找到 {len(files)} 个文件:\n")
        for i, f in enumerate(files, 1):
            print(f"  {i}. {str(f)}")
        print()

        # 创建输出目录
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)

        root = Path(directory)
        result = None
        # 处理统计
        stats = {
            "total_files": len(files),
            "success_count": 0,
            "failed_count": 0,
            "files": []
        }

        # 为每个文件单独生成输出
        print("\n" + "=" * 70)
        print("模式：为每个文件单独生成输出")
        print("=" * 70)

        for i, file_path in enumerate(files, 1):
            print(f"\n[{i}/{len(files)}] 处理：{file_path.name}")
            print("-" * 70)
            absrel = file_path.relative_to(root)
            is_image = is_image_by_ext(absrel)
            if only_path:
                content = f"附件路径: {file_path}"
                prompt.append({"type":"text","text":content})
            else:
                # 读取文件
                if not is_image:
                    content = self.read_file(file_path)
                    if not content:
                        print(f"  x 跳过（无法读取）")
                        stats["failed_count"] += 1
                        stats["files"].append({
                            "file": file_path.name,
                            "error": "无法读取文件",
                            "success": False
                        })
                        continue
                    print(f"  读取成功 ({len(content)} 字符)")
                    prompt.append({"type":"text","text":content})
                else:
                    image_data = file_path.read_bytes()
                    mime = guess_image_mime(file_path)
                    b64 = base64.b64encode(image_data).decode("utf-8")
                    prompt.append({"type": "image", "image_url": f"data:{mime};base64,{b64}"})

            print(f"  开始生成输出...")

            # 生成输出
            result = self.prompt(
                content=prompt,
                workspace=str(absrel.parent),
                session_id=session_id
            )
            if result["success"]:
                stats["success_count"] += 1
                # 保存结果
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_name = absrel.replace(' ', '_').replace('-', '_')
                if output_dir:
                    output_file = Path(output_dir) / f"output_{safe_name}_{timestamp}.md"

                    # 添加头部信息
                    header = f"""# 输入文件: {file_path.name}\n"""

                    with open(output_file, 'w', encoding='utf-8') as f:
                        f.write(header + result["content"])

                    print(f"\n  输出已保存：{output_file}")
                    stats["files"].append({
                        "file": file_path.name,
                        "output_file": str(output_file),
                        "success": True
                    })
                else:
                    stats["files"].append({
                        "file": file_path.name,
                        "output_content": result["content"],
                        "success": True
                    })
            else:
                stats["failed_count"] += 1
                stats["files"].append({
                    "file": file_path.name,
                    "error": result["error"],
                    "success": False
                })

        if len(files)==1:
            return result

        # 打印统计
        print("\n" + "=" * 70)
        print("处理完成统计")
        print("=" * 70)
        print(f"总文件数：{stats['total_files']}")
        print(f"成功：{stats['success_count']}")
        print(f"失败：{stats['failed_count']}")
        print(f"输出目录：{output_dir}")
        print("=" * 70)
        result['content'] = stats
        return result

    def _build_image_block(self, image_ref: str, base_dir: Path) -> Dict | None:
        """
        将 markdown 中的图片引用转换为 image 块

        Args:
            image_ref: 图片引用（本地路径或远程 URL）
            base_dir: markdown 文件所在目录，用于解析相对路径

        Returns:
            Dict | None: image 块，加载失败时返回 None
        """
        # 远程图片：直接使用 URL
        if image_ref.startswith(("http://", "https://")):
            return {"type": "image", "image_url": image_ref}

        # 本地图片：相对路径基于 markdown 文件所在目录解析
        image_path = Path(image_ref)
        if not image_path.is_absolute():
            image_path = base_dir / image_path

        if not image_path.exists():
            print(f"  x 图片不存在：{image_path}")
            return None

        try:
            image_data = image_path.read_bytes()
            mime = guess_image_mime(image_path)
            b64 = base64.b64encode(image_data).decode("utf-8")
            return {"type": "image", "image_url": f"data:{mime};base64,{b64}"}
        except Exception as e:
            print(f"  x 读取图片失败（{image_path}）：{e}")
            return None

    def _parse_markdown_blocks(self, markdown_content: str, base_dir: Path) -> List[Dict]:
        """
        将 markdown 内容解析为有序的块列表：
        文本内容 -> text 块，图片引用 -> image 块

        Args:
            markdown_content: markdown 文本内容
            base_dir: markdown 文件所在目录，用于解析图片相对路径

        Returns:
            List[Dict]: 按出现顺序排列的 text/image 块
        """
        blocks: List[Dict] = []
        # 匹配 markdown 图片引用：![alt](path) 或 ![alt](path "title")
        image_pattern = re.compile(
            r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+["\'][^"\']*["\'])?\)'
        )

        last_end = 0
        for match in image_pattern.finditer(markdown_content):
            # 图片引用之前的文本
            text = markdown_content[last_end:match.start()]
            if text.strip():
                blocks.append({"type": "text", "text": text})

            alt = match.group(1)
            image_ref = match.group(2)
            image_block = self._build_image_block(image_ref, base_dir)
            if image_block:
                blocks.append(image_block)
            else:
                # 图片加载失败，回退为文本提示
                blocks.append({"type": "text", "text": f"[图片无法加载: {alt or image_ref}]"})

            last_end = match.end()

        # 末尾剩余的文本
        tail = markdown_content[last_end:]
        if tail.strip():
            blocks.append({"type": "text", "text": tail})

        return blocks

    # -----------------------------------------------------------------------
    # 超长 Markdown：大纲导航 + 按需读取
    # -----------------------------------------------------------------------
    def _extract_outline(self, markdown_content: str):
        """
        解析 Markdown 标题层级，生成大纲与章节映射。

        Args:
            markdown_content: Markdown 全文

        Returns:
            (outline_lines, sections, preamble)
            outline_lines: 供模型阅读的大纲行（含编号、标题、字符数）
            sections: {"1.2": {"level":2,"title":"...","content":"..."}, ...}
            preamble: 第一个标题之前的正文前内容（如摘要、前言）
        """
        lines = markdown_content.splitlines()
        raw_headings = []  # (level, title, line_index)
        for i, line in enumerate(lines):
            m = _HEADING_RE.match(line)
            if m:
                raw_headings.append((len(m.group(1)), m.group(2).strip(), i))

        # 无任何标题：返回空大纲，交由调用方退化处理
        if not raw_headings:
            return [], {}, markdown_content

        # 仅有一个标题（无实质结构）：退化处理
        if len(raw_headings) == 1:
            return [], {}, markdown_content

        # 常见论文结构：`# 标题`（唯一顶层标题）+ `## 章节`。
        # 此时把标题及其后的正文前内容（如摘要）并入 preamble，避免标题章节吞噬全文。
        min_level = min(h[0] for h in raw_headings)
        min_level_count = sum(1 for h in raw_headings if h[0] == min_level)
        title_only = (
            min_level_count == 1
            and raw_headings[0][0] == min_level
            and len(raw_headings) > 1
        )

        if title_only:
            body_headings = raw_headings[1:]
            preamble = "\n".join(lines[: body_headings[0][2]])
            min_level = min(h[0] for h in body_headings)
        else:
            body_headings = raw_headings
            preamble = "\n".join(lines[: raw_headings[0][2]])

        # 以最浅层级作为顶层，重新归一化层级编号
        counts = [0] * 7
        metas = []  # (sid, norm_level, start_line, title)
        for level, title, start_line in body_headings:
            norm_level = level - min_level + 1
            counts[norm_level] += 1
            for l in range(norm_level + 1, 7):
                counts[l] = 0
            sid = ".".join(str(counts[l]) for l in range(1, norm_level + 1))
            metas.append((sid, norm_level, start_line, title))

        # 每个章节的结束行 = 下一个「层级 <= 自身层级」的标题行（或文件末尾），
        # 这样父章节会包含其所有子章节内容，避免跨章逻辑被切断。
        sections = {}
        total_lines = len(lines)
        for idx, (sid, level, start_line, title) in enumerate(metas):
            end_line = total_lines
            for _sid2, level2, start_line2, _title2 in metas[idx + 1:]:
                if level2 <= level:
                    end_line = start_line2
                    break
            content = "\n".join(lines[start_line:end_line])
            sections[sid] = {
                "level": level,
                "title": title,
                "content": content,
                "start_line": start_line,
                "end_line": end_line,
            }

        outline_lines = []
        if preamble.strip():
            outline_lines.append(f"[0] （正文前内容） {len(preamble)} 字符")
        for sid, level, _start_line, title in metas:
            indent = "  " * (level - 1)
            outline_lines.append(f"{indent}[{sid}] {title}（{len(sections[sid]['content'])} 字符）")

        return outline_lines, sections, preamble

    def _split_section_content(self, label: str, content: str, max_chars: int = MAX_SECTION_CHARS):
        """
        将一段内容按段落切块，使每块不超过 max_chars。

        Returns:
            List[(label, text)]: 若无需切块返回单元素；否则返回带「第 x/n 段」标注的块。
        """
        if len(content) <= max_chars:
            return [(label, content)]

        paragraphs = re.split(r'\n\s*\n', content)
        pieces = []
        current = ""
        for p in paragraphs:
            if current and len(current) + len(p) + 2 <= max_chars:
                current += "\n\n" + p
            elif not current and len(p) <= max_chars:
                current = p
            else:
                if current:
                    pieces.append(current)
                    current = ""
                # 单个段落超过上限时硬切
                while len(p) > max_chars:
                    pieces.append(p[:max_chars])
                    p = p[max_chars:]
                current = p
        if current:
            pieces.append(current)

        if len(pieces) <= 1:
            return [(label, content)]
        return [(f"{label}（第 {i + 1}/{len(pieces)} 段）", piece) for i, piece in enumerate(pieces)]

    def _parse_navigation_response(self, text: str):
        """
        解析模型在大纲导航阶段的回复。

        Returns:
            (action, sections, answer)
            action: "read" 表示需要读取章节，"done" 表示已给出最终答案
            sections: 需要读取的章节编号列表（action == "read" 时有效）
            answer: 最终答案（action == "done" 时有效）
        """
        # READ 指令：READ: 1.2,3,0
        read_match = re.search(r'^\s*READ\s*[:：]\s*(.+)$', text, re.IGNORECASE | re.MULTILINE)
        if read_match:
            ids = re.findall(r'[0-9]+(?:\.[0-9]+)*', read_match.group(1))
            if ids:
                return "read", ids, None

        # DONE 指令：单独一行 DONE，其后为最终回答
        done_match = re.search(r'^\s*DONE\s*[:：]?\s*$', text, re.IGNORECASE | re.MULTILINE)
        if done_match:
            answer = text[done_match.end():].strip()
            if not answer:
                answer = text.strip()
            return "done", [], answer

        # 未识别到指令：视为最终回答
        return "done", [], text.strip()

    def _build_section_payload(self, sections, preamble, requested_ids, sent_sections, base_dir: Path):
        """
        根据章节编号读取内容，转换为可发送的块列表，并记录已发送章节。

        Returns:
            List[Dict]: text/image 块列表
        """
        blocks = []
        for sid in requested_ids:
            if sid in sent_sections:
                continue
            sent_sections.add(sid)

            if sid == "0":
                content = preamble
                title = "正文前内容"
            elif sid in sections:
                content = sections[sid]["content"]
                title = sections[sid]["title"]
            else:
                blocks.append({"type": "text", "text": f"[{sid}] 章节不存在，已跳过。\n"})
                continue

            pieces = self._split_section_content(sid, content)
            for label, piece_text in pieces:
                blocks.append({"type": "text", "text": f"\n===== [{label}] {title} =====\n"})
                blocks.extend(self._parse_markdown_blocks(piece_text, base_dir))
        return blocks

    def _ask_large_markdown_chunked(self, md_file: Path, question, system_prompt, session_id: str, markdown_content: str) -> Dict:
        """
        无标题结构的超长 Markdown 退化方案：按固定大小切块，分轮发送后再汇总作答。
        """
        chunks = self._split_section_content("document", markdown_content)
        print(f"  文档无标题结构，退化为顺序切块（{len(chunks)} 块）")

        conversation = []
        if system_prompt:
            conversation.append({"role": "system", "content": system_prompt})
        conversation.append({
            "role": "user",
            "content": [
                {"type": "text", "text": "以下是一个超长文档，已按顺序切块。请逐段阅读，全部读完后我会请你作答。\n<question>\n"},
                *question,
                {"type": "text", "text": "\n</question>"},
            ],
        })

        # 按轮次批量发送，每轮不超过字符预算，减少请求次数
        rounds = []
        cur_blocks = []
        cur_chars = 0
        for i, (_label, text) in enumerate(chunks, 1):
            piece_blocks = self._parse_markdown_blocks(text, md_file.parent)
            if cur_chars + len(text) > MAX_SECTION_CHARS_PER_ROUND and cur_blocks:
                rounds.append(cur_blocks)
                cur_blocks = []
                cur_chars = 0
            cur_blocks.append({"type": "text", "text": f"\n--- 文档第 {i}/{len(chunks)} 段 ---\n"})
            cur_blocks.extend(piece_blocks)
            cur_chars += len(text)
        if cur_blocks:
            rounds.append(cur_blocks)

        result = None
        for ri, round_blocks in enumerate(rounds, 1):
            conversation.append({"role": "user", "content": round_blocks})
            result = self._post_chat(conversation, workspace=str(md_file.parent), session_id=session_id)
            if not result["success"]:
                result["file"] = str(md_file)
                return result
            print(f"  已发送第 {ri}/{len(rounds)} 批")

        # 全部读取完毕后请求最终答案
        conversation.append({
            "role": "user",
            "content": [{"type": "text", "text": "以上为文档全文，请基于全文给出完整、准确的最终答案。"}],
        })
        result = self._post_chat(conversation, workspace=str(md_file.parent), session_id=session_id)
        result["file"] = str(md_file)
        return result

    def _ask_large_markdown(self, md_file: Path, question: List[Dict], system_prompt: List[Dict], session_id: str = "") -> Dict:
        """
        对超过阈值的超长 Markdown 采用「大纲导航 + 按需读取」：

        1. 提取标题大纲（编号 + 标题 + 字符数）发给模型；
        2. 模型根据任务判断相关章节，按需请求读取（READ: 编号）；
        3. 读取对应章节（父章节含子章节，避免跨段逻辑被切断）发送给模型；
        4. 多轮交叉核对，直到模型输出 DONE 给出最终答案。

        Returns:
            Dict: 与 ask_markdown 单文件一致的 {"file","content","success","error"}
        """
        print("  检测到超大 Markdown（> 1MB），启用「大纲导航 + 按需读取」策略")
        try:
            markdown_content = md_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                markdown_content = md_file.read_text(encoding="gbk")
            except Exception as e:
                return {"file": str(md_file), "success": False, "error": f"读取失败：{e}"}
        except Exception as e:
            return {"file": str(md_file), "success": False, "error": f"读取失败：{e}"}

        outline_lines, sections, preamble = self._extract_outline(markdown_content)

        # 无标题结构：退化为顺序切块
        if not sections:
            return self._ask_large_markdown_chunked(md_file, question, system_prompt, session_id, markdown_content)

        outline_text = "\n".join(outline_lines)
        print(f"  已提取大纲：{len(sections)} 个章节 + {('正文前内容' if preamble.strip() else '无')}")

        instructions = (
            "这是一个超长 Markdown 文档，为避免超出上下文限制，采用「大纲导航 + 按需读取」方式处理。\n"
            "下面给出文档大纲（[编号] 标题（字符数））。请先根据任务需求，判断哪些章节可能包含相关信息，再按需请求读取。\n\n"
            "回复格式（严格遵守，请勿输出多余内容）：\n"
            "- 需要读取某些章节时，只输出一行：READ: 1.2,3,0\n"
            "- 已获取足够信息、可以给出最终答案时，先输出单独一行：DONE\n"
            "  然后在其后各行给出完整回答。\n"
            "注意：不要在 READ 阶段输出最终答案，也不要编造未读取章节的内容；"
            "信息可能散落在多个章节，请逐一读取后交叉汇总。\n\n"
            "=== 文档大纲 ===\n"
            f"{outline_text}\n"
            "=== 大纲结束 ===\n"
        )

        conversation = []
        if system_prompt:
            conversation.append({"role": "system", "content": system_prompt})
        conversation.append({
            "role": "user",
            "content": [
                {"type": "text", "text": instructions + "\n<question>\n"},
                *question,
                {"type": "text", "text": "\n</question>"},
            ],
        })

        sent_sections = set()
        final_result = None
        result = None

        for round_no in range(1, MAX_OUTLINE_ROUNDS + 1):
            print(f"  [大纲导航] 第 {round_no}/{MAX_OUTLINE_ROUNDS} 轮")
            result = self._post_chat(conversation, workspace=str(md_file.parent), session_id=session_id)
            if not result["success"]:
                result["file"] = str(md_file)
                return result

            action, requested, answer = self._parse_navigation_response(result["content"])

            if action == "read" and requested:
                new_sections = [s for s in requested if s not in sent_sections]
                if not new_sections:
                    print("  ! 模型未请求新章节，转为请求最终答案")
                    conversation.append({"role": "assistant", "content": [{"type": "text", "text": result["content"]}]})
                    conversation.append({"role": "user", "content": [{"type": "text", "text": "请直接基于已读取的内容给出最终完整答案。"}]})
                    result = self._post_chat(conversation, workspace=str(md_file.parent), session_id=session_id)
                    final_result = result
                    break

                payload = self._build_section_payload(sections, preamble, new_sections, sent_sections, md_file.parent)
                if not payload:
                    print("  ! 请求的章节不可读，转为请求最终答案")
                    conversation.append({"role": "assistant", "content": [{"type": "text", "text": result["content"]}]})
                    conversation.append({"role": "user", "content": [{"type": "text", "text": "你请求的章节不存在或为空，请基于已读取的内容给出最终完整答案。"}]})
                    result = self._post_chat(conversation, workspace=str(md_file.parent), session_id=session_id)
                    final_result = result
                    break

                print(f"  读取章节：{', '.join(new_sections)}")
                conversation.append({"role": "assistant", "content": [{"type": "text", "text": result["content"]}]})
                conversation.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"已读取以下章节内容（{', '.join(new_sections)}）：\n"},
                        *payload,
                        {"type": "text", "text": "\n\n请基于以上内容继续：若还需其他章节，回复 READ: 编号；否则回复 DONE 并给出最终答案。"},
                    ],
                })
            else:
                if action == "done" and answer:
                    result["content"] = answer
                final_result = result
                break

        if final_result is None:
            # 达到最大轮数仍未给出最终答案：要求模型基于已读内容作答
            print(f"  ! 已达最大轮数（{MAX_OUTLINE_ROUNDS}），请求基于已读内容给出最终答案")
            conversation.append({
                "role": "user",
                "content": [{"type": "text", "text": "已达到最大读取轮数，请基于已读取的内容给出最终完整答案。"}],
            })
            result = self._post_chat(conversation, workspace=str(md_file.parent), session_id=session_id)
            final_result = result

        final_result["file"] = str(md_file)
        return final_result

    def ask_markdown(self,
            prompt: List[Dict],
            system_prompt: List[Dict],
            directory: List[str]|str,
            session_id: str = ""
    ) -> Dict:
        """
        将 markdown 文件发送给多模态大模型，逐块发送：
        遇到文本发送 text 块，遇到图片发送 image 块。

        Args:
            prompt: 用户提示词
            directory: markdown 文件路径，或包含 markdown 文件的目录（单个或列表）
            session_id: 会话 ID

        Returns:
            Dict: 发送结果，单个文件时直接返回该文件的生成结果
        """
        # 收集 markdown 文件
        paths = directory if isinstance(directory, list) else [directory]
        md_files: List[Path] = []
        for p in paths:
            p = Path(p)
            if p.is_file():
                if p.suffix.lower() in (".md", ".markdown"):
                    md_files.append(p)
            elif p.is_dir():
                md_files.extend(sorted(
                    f for f in p.rglob("*")
                    if f.is_file()
                    and f.suffix.lower() in (".md", ".markdown")
                    and not f.name.startswith('.')
                ))

        if not md_files:
            print("x 未找到任何 markdown 文件")
            return {"success": False, "error": "未找到 markdown 文件"}

        print(f"找到 {len(md_files)} 个 markdown 文件:\n")
        for i, f in enumerate(md_files, 1):
            print(f"  {i}. {str(f)}")
        print()

        results: List[Dict] = []
        last_result: Dict | None = None

        for i, md_file in enumerate(md_files, 1):
            print(f"\n[{i}/{len(md_files)}] 发送：{md_file.name}")
            print("-" * 70)

            # 超长 Markdown（> 1MB）：改用「大纲导航 + 按需读取」，避免一次性塞入上下文
            try:
                file_size = md_file.stat().st_size
            except OSError:
                file_size = 0
            if file_size > LARGE_MD_SIZE_THRESHOLD:
                result = self._ask_large_markdown(md_file, prompt, system_prompt, session_id)
                results.append(result)
                last_result = result
                if result.get("success"):
                    print("  发送成功")
                else:
                    print(f"  x 发送失败：{result.get('error')}")
                continue

            # 读取 markdown 内容
            try:
                markdown_content = md_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    markdown_content = md_file.read_text(encoding="gbk")
                except Exception as e:
                    print(f"  x 读取失败：{e}")
                    results.append({"file": str(md_file), "success": False, "error": str(e)})
                    continue
            except Exception as e:
                print(f"  x 读取失败：{e}")
                results.append({"file": str(md_file), "success": False, "error": str(e)})
                continue

            # 解析为 text/image 块
            blocks = self._parse_markdown_blocks(markdown_content, md_file.parent)
            if not blocks:
                print("  x 没有可发送的内容")
                results.append({"file": str(md_file), "success": False, "error": "没有可发送的内容"})
                continue

            text_count = sum(1 for b in blocks if b.get("type") == "text")
            image_count = sum(1 for b in blocks if b.get("type") == "image")
            print(f"  解析出 {len(blocks)} 个块（文本 {text_count}，图片 {image_count}）")

            msg = []
            msg.append({"type":"text","text": f"\n<document filename=\"{md_file.name}>\"\n"})
            msg.extend(blocks)
            msg.append({"type":"text","text":"\n</document>\n<question>\n"})
            msg.extend(prompt)
            msg.append({"type":"text","text":"\n</question>\n"})
            # 发送给多模态大模型
            result = self.prompt(
                content=msg,
                workspace=str(md_file.parent),
                system_prompt=system_prompt,
                session_id=session_id
            )
            result["file"] = str(md_file)
            results.append(result)
            last_result = result

            if result.get("success"):
                print("  发送成功")
            else:
                print(f"  x 发送失败：{result.get('error')}")

        if len(md_files) == 1 and last_result is not None:
            return last_result

        total = len(results)
        success_count = sum(1 for r in results if r.get("success"))
        failed_count = total - success_count

        print("\n" + "=" * 70)
        print("发送完成统计")
        print("=" * 70)
        print(f"总文件数：{total}")
        print(f"成功：{success_count}")
        print(f"失败：{failed_count}")
        print("=" * 70)

        return {
            "success": failed_count == 0,
            "content": results,
            "error": None if failed_count == 0 else f"{failed_count} 个文件发送失败"
        }
