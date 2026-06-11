from __future__ import annotations

from manager.state import FirstMateState
from shared.models import JobStatus


async def report_result(state: FirstMateState) -> dict:
    status = state.get("status", JobStatus.COMPLETED)
    findings = state.get("findings", [])
    hypothesis = state.get("hypothesis", "")
    runner_output = state.get("runner_output")

    if runner_output:
        report = (
            f"✅ *Hoàn thành*\n\n"
            f"*Kết quả:*\n{runner_output}"
        )
    else:
        findings_text = "\n".join(f"• {f}" for f in findings) if findings \
            else "_Không có findings đặc biệt_"
        hypothesis_text = f"\n\n💡 *Hypothesis:* {hypothesis}" \
            if hypothesis else ""
        report = f"📋 *FirstMate-Manager*\n\n{findings_text}{hypothesis_text}"

    return {
        "final_report": report,
        "status": JobStatus.COMPLETED,
    }


async def escalated(state: FirstMateState) -> dict:
    attempted = state.get("assignment_attempts", [])
    reason = (
        "Không có SRE online" if not attempted
        else f"Đã thử {len(attempted)} SRE nhưng không available: "
             + ", ".join(attempted)
    )

    report = (
        f"⚠️ *Escalated — cần xử lý thủ công*\n\n"
        f"Lý do: {reason}\n\n"
        f"Vui lòng liên hệ trực tiếp hoặc tạo incident ticket."
    )

    return {
        "final_report": report,
        "status": JobStatus.ESCALATED,
    }
