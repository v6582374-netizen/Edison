# Separate workspace access, Google authorization and YouTube content sessions

**Status: accepted (2026-10-19); implementation and production acceptance remain outstanding.**

Edison sign-in controls access to the workspace; it does not own background YouTube permissions. The existing connection module exclusively maintains Google authorization. A separate, narrowly scoped content-session module manages an on-demand server browser whose dedicated profile is the persistent source for content acquisition. Independent acquisition workers consume isolated snapshots and cannot overwrite that source. This avoids both dependence on an online personal Mac and competing durable Cookie writers, without adding a second general-purpose authentication guardian.

This refines ADR 0013: retiring local desktop browser automation does not prohibit a server browser dedicated to maintaining this content session. It leaves the trusted-device handoff for initial official OAuth authorization intact. Ordinary session maintenance may run unattended; explicit Google security challenges return control to the person. Natural rotation and resource coexistence remain unproven, so acceptance can require reopening the content-path choice.

The canonical responsibility, disconnection and recovery decision is [谁负责三层身份的持久化与恢复](https://github.com/vincentshuo/Edison/issues/44#issuecomment-6258193015). The accepted, not-yet-executed acceptance contract is [什么情况下自动恢复、什么情况下才打断用户](https://github.com/vincentshuo/Edison/issues/45#issuecomment-6258398691); the accepted, not-yet-executed migration decision is [现有授权与旧入口如何切换到服务器会话](https://github.com/vincentshuo/Edison/issues/46#issuecomment-6258668775).
