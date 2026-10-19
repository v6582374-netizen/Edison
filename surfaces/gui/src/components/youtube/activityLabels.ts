import { yt } from "./text";

export function activityLabels(): Record<string, string> {
  return {
    acquiring: yt("获取字幕"), generating: yt("准备生成"), initial: yt("初译"), review: yt("审校"),
    revision: yt("修订"), composition: yt("整理成稿"), ready: yt("已生成"), failed: yt("处理失败"),
    unavailable: yt("暂不可用"), cancelled: yt("已取消"), restored: yt("恢复排队"), queued: yt("已排队"),
    checking: yt("确认视频类型"), awaiting_classification: yt("待确认类型"), filtered: yt("已排除 Shorts"),
    waiting_retry: yt("等待重试"), rate_limited: yt("限流等待"), expired: yt("已过期"), awaiting_timing: yt("待核实时间"),
    awaiting_completion: yt("等待视频结束"), commenced: yt("已实际开工"),
  };
}
