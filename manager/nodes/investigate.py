from __future__ import annotations

# ChatOpenAI is used as an OpenAI-compatible client pointing to GreenNode MaaS endpoint
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from manager.state import FirstMateState
from manager.tools.k8s_tool import K8S_READ_TOOLS
from shared.models import Command, Permission

SYSTEM_PROMPT = """\
Bạn là FirstMate-Manager, AI assistant cho DevOps/SRE team.

NHIỆM VỤ:
1. Phân tích yêu cầu từ Dev/QC
2. Dùng các tools để thu thập thông tin (read-only)
3. Tổng hợp findings và đề xuất action nếu cần

QUY TẮC:
- Chỉ dùng tools được cung cấp — KHÔNG tự tạo command
- Tools đều là read-only, an toàn để chạy
- Nếu cần WRITE op (delete/scale/restart/update), đánh dấu rõ là WRITE_OP
- Trả lời bằng tiếng Việt, ngắn gọn, súc tích

FORMAT KHI CÓ WRITE OP:
FINDINGS: [list những gì tìm được]
HYPOTHESIS: [nguyên nhân có thể]
WRITE_OPS:
  - description: "mô tả việc cần làm"
    cmd: "kubectl rollout restart deployment/payment -n production"
    tool: k8s

Nếu chỉ cần read, trả lời trực tiếp không cần format trên.
"""

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        import os
        _llm = ChatOpenAI(
            model="qwen/qwen3-5-27b",
            base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
            api_key=os.environ["GREENNODE_API_KEY"],
            temperature=0,
        ).bind_tools(K8S_READ_TOOLS)
    return _llm


def _parse_write_ops(text: str) -> list[Command]:
    """Parse WRITE_OPS section từ LLM response."""
    commands = []
    if "WRITE_OPS:" not in text:
        return commands

    import re
    # Tìm các block cmd: "..."
    for match in re.finditer(
        r'description:\s*"([^"]+)"\s+cmd:\s*"([^"]+)"\s+tool:\s*(\w+)',
        text
    ):
        commands.append(Command(
            description=match.group(1),
            cmd=match.group(2),
            permission=Permission.WRITE,
            tool=match.group(3),
        ))
    return commands


async def investigate(state: FirstMateState) -> dict:
    llm = _get_llm()

    messages = state.get("messages", [])
    if not messages:
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=state["user_message"]),
        ]

    # Tool calling loop
    while True:
        response = await llm.ainvoke(messages)
        messages.append(response)

        # Nếu không còn tool call → xong
        if not getattr(response, "tool_calls", None):
            break

        # Thực thi tools
        from langchain_core.messages import ToolMessage
        tool_map = {t.name: t for t in K8S_READ_TOOLS}
        for tc in response.tool_calls:
            tool_fn = tool_map.get(tc["name"])
            if tool_fn:
                result = await tool_fn.ainvoke(tc["args"])
                messages.append(ToolMessage(
                    content=str(result),
                    tool_call_id=tc["id"],
                ))

    final_text = response.content if isinstance(response.content, str) \
        else str(response.content)

    write_ops = _parse_write_ops(final_text)

    # Extract findings (text trước WRITE_OPS hoặc toàn bộ nếu không có)
    findings_text = final_text.split("WRITE_OPS:")[0].strip()
    findings = [l.strip("- ").strip()
                for l in findings_text.splitlines() if l.strip()]

    return {
        "messages": messages,
        "findings": findings,
        "write_ops": write_ops,
        "final_report": final_text if not write_ops else None,
    }
