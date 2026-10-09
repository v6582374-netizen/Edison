# Edison

Edison's product language covers the YouTube reading workbench, items object memory, minimalism focus, and Curriculum.

## YouTube reading language

- **Content freshness（内容新鲜度）**: The age of a source video measured from its freshness reference time, independently of when Edison first observes it. _Avoid_: Discovery recency, latest imported item.
- **Freshness reference time（新鲜度起算时刻）**: The reference for a video's automatic-preparation age: its public publication time for an ordinary video, or its reliably established actual end time for a live stream or YouTube Premiere. It does not replace the original publication time. _Avoid_: Discovery time, scheduled end, subtitle availability time.
- **Event completion evidence（事件结束凭据）**: Official video details matching the source channel, public and processed, with a non-active broadcast status and timezone-aware actual start/end times in chronological order. Scheduled times and local observations never establish completion. The API does not expose a dedicated Premiere discriminator. _Avoid_: Duration-based end estimates, discovery as completion.
- **YouTube Premiere（YouTube 首映）**: A prerecorded video initially presented as a scheduled shared viewing event before becoming an ordinary on-demand video. _Avoid_: Real-time live production, film genre.
- **Pending timing verification（待核实时间）**: A discovered video whose required publication time, actual end time or completion status cannot yet be reliably established. It cannot begin automatic preparation on an assumed age. _Avoid_: Newly published, preparation already commenced.
- **Automatic preparation eligibility（自动处理资格）**: Whether a subscription-discovered video currently qualifies to begin automatic manuscript preparation; being queued does not grant permanent eligibility. Losing eligibility does not delete retained knowledge assets. _Avoid_: Retention period, reading priority, manual-link admission.
- **Manual preparation request（手动处理请求）**: A person's explicit request to prepare a particular video, including obtaining older content or regenerating a manuscript. It expresses content-specific intent distinct from enabling or resuming updates globally. _Avoid_: Global resume, automatic retry, subscription import.
- **Runnable preparation work（可运行的处理工作）**: Work whose prerequisites, retry timing and current execution permission allow its next processing stage to proceed. Age eligibility or a place in the queue alone does not make it runnable. _Avoid_: Every pending task, guaranteed immediate execution.
- **Preparation priority（处理优先级）**: The relative preference for assigning available processing capacity among runnable tasks. It neither interrupts active work nor grants age eligibility. _Avoid_: Completion eligibility, fixed delivery deadline.
- **Manuscript concurrency limit（成稿并发上限）**: The person's selected maximum number of videos simultaneously undergoing translation and manuscript composition. It is distinct from subtitle acquisition capacity, discovery frequency, and provider account limits. _Avoid_: API quota, stage count, number of retained logs.
- **Video task console（视频任务控制台）**: The processing log for one video's particular preparation task, selected by its video title for inspection. Viewing a console does not start, pause or prioritize its task. _Avoid_: Worker slot, combined output from multiple videos.
- **Preparation commencement（处理开工）**: The actual beginning of transcript acquisition, or translation/manuscript work when a usable transcript is already retained, after prerequisite checks. _Avoid_: Discovery, queue entry, task reservation, format classification.
- **Automatic start window（自动开工窗口）**: The inclusive rolling 72 hours from a corroborated, timezone-aware public publication time for ordinary videos. Admission and first actual work each check it. Missing, conflicting or future time waits for verification. For an identified live stream or Premiere, the same inclusive 72-hour window starts at its verified actual end; a future schedule is retained as waiting work. Timing is rechecked at first work, and commenced tasks retain age eligibility through interruption. _Avoid_: Discovery age, retention deadline.
- **Commencement evidence（开工凭据）**: A durable fact recorded immediately before actual subtitle I/O after prerequisites, or before translation/composition with usable retained captions. Legacy reservation timestamps are insufficient; a readable, identity-matched transcript can establish prior work. Ordinary retries retain the fact; cancellation or explicit regeneration ends that task’s eligibility. _Avoid_: Claim timestamp, classification event, blanket exemption for a video.
- **Automatic completion eligibility（自动任务完成资格）**: The continuing age eligibility of an automatic preparation task that has actually commenced, retained through interruption until that task is finished or cancelled. It grants neither priority nor unlimited retries. _Avoid_: Permanent video exemption, guaranteed source availability.
- **Auto Update（自动更新）**: The person's persistent permission for subscription discovery and subsequent automatic task claims, including interrupted work. It defaults off and preserves the last explicit choice through normal exit and crashes. It does not govern explicitly requested manual video work. _Avoid_: Global processing switch, manual request permission.
- **Manual link submission（手动链接提交）**: A person's explicit provision of a YouTube video URL that, once accepted, authorizes its processing independently of Auto Update while sharing the downstream pipeline and reading assets with subscription-discovered videos. _Avoid_: Age exemption, subscription backfill, separate manuscript library.
- **Subscription intake scope（订阅自动获取范围）**: The channels whose videos are considered through automatic subscription discovery. Excluding a channel from this scope does not prevent an explicit manual request for one of its videos or change shared content-processing conditions. _Avoid_: Video access permission, global channel ban.
- **Repeated video submission（重复视频提交）**: Another explicit submission of the same video, resolving to its existing task or document regardless of URL form or discovery source. It is not an instruction to regenerate a manuscript or restore cancelled work. _Avoid_: New video, automatic regeneration, implicit restoration.
- **Accepted video request（已接收的视频请求）**: A video preparation request successfully saved by Edison, distinct from a link still present in the input field. Acceptance neither means preparation has begun nor overrides shared processing conditions. _Avoid_: Pasted URL, started preparation, available manuscript.
- **Discovery latency（发现延迟）**: The elapsed time from a video's publication to Edison's first discovery of that video. _Avoid_: Polling interval, manuscript completion time.
- **Page-relative discovery latency（相对网页的发现延迟）**: The interval between a video becoming visible in the channel's latest-video listing under a specified access context and Edison discovering it. It is distinct from the video's age and from another source's propagation delay. _Avoid_: Feed polling interval, publication age, manuscript delivery latency.
- **Manuscript delivery latency（成稿交付延迟）**: The elapsed time from a video's publication until its Chinese reading manuscript is available to the person. It includes discovery, waiting and preparation. _Avoid_: Model response time, discovery latency.
- **Recovery catch-up（恢复补漏）**: The bounded discovery of missed videos still relevant to the person's automatic preparation scope after an interruption. _Avoid_: Complete channel history, automatic restoration of cancelled work.
- **Incomplete catch-up（补查未完成）**: A discovery pass whose bounded checks have not established coverage of the intended subscription window. It is distinct from finding no eligible new videos. _Avoid_: Fully synchronized, empty channel.
- **Awaiting video completion（等待视频结束）**: A discovered live or premiere video that has not yet finished and cannot begin transcript acquisition or manuscript preparation. Waiting is not preparation commencement or proof of subtitle availability. Explicit upcoming/live status appears as awaiting completion; absent or contradictory completion evidence appears as pending timing verification. Known waiting videos receive bounded official detail rechecks independently of whether they remain on the latest uploads page. _Avoid_: Already started preparation, failed manuscript.
- **YouTube publication time（YouTube 发布时间）**: The video's public publication time on YouTube, retained independently of its actual end time and Edison's observation times. _Avoid_: Update time, local discovery time, retrieval time, title modification time.
- **Video duration（视频时长）**: The playback length of the source YouTube video. It is distinct from the time a person needs to read its manuscript. _Avoid_: Estimated reading time, preparation time.
- **Video metadata（视频基础信息）**: The source channel, YouTube publication time and video duration that identify and contextualize a video across subscription discovery, preparation progress and the reading library. _Avoid_: Preparation status, processing diagnostics, popularity ranking.
- **YouTube Short（Shorts）**: A video classified by YouTube as part of its Shorts format. A short duration alone does not make an ordinary video a Short. _Avoid_: All short videos, videos under three minutes.
- **Shorts exclusion（Shorts 排除）**: The automatic exclusion of platform Shorts from new manuscript preparation, including previously queued work. Existing manuscripts remain retained knowledge assets.
- **Pending classification（待确认类型）**: A discovered video whose platform format cannot yet be established. It waits for another check without acquiring subtitles or generating a manuscript.
- **YouTube cooldown（YouTube 冷却）**: A shared waiting period imposed after YouTube limits requests. It suspends further YouTube acquisition across the workbench while preserving existing reading assets, and survives restart or manual queue changes.
- **Rate-limit waiting（限流等待）**: A video's recoverable preparation state while platform access is restricted. It can resume after cooldown or be cancelled; repeated throttling eventually requires an explicit retry.
- **Subscription source**: A YouTube channel imported from the person's connected account whose videos may be considered for automatic preparation. The channel is distinct from the interface used to discover its videos. _Avoid_: RSS feed, manual video submission.
- **Candidate**: A newly discovered subscription update that has not yet earned the user's reading time.
- **Batch manuscript preparation**: The deliberate choice to acquire content and generate a full Chinese deep-reading manuscript for discovered updates before the person chooses what to read, except updates whose preparation the person has cancelled.
- **Manual queue review**: The first-release selection model: a person browses prepared manuscripts and decides which deserve attention; no autonomous relevance ranking is applied.
- **Triage**: A later, optional AI-assisted prioritization of prepared manuscripts. It may suggest priorities but never decides what the person must read.
- **Inbox**: The complete set of newly prepared manuscripts in the person's Edison instance awaiting their review.
- **Reading queue**: The set of manuscripts the person deliberately moves from the inbox to read later; opening one begins reading and completing it preserves it as read history.
- **Reading state**: One of inbox, to-read, reading or read; it organizes attention without controlling retention.
- **Retention**: Every prepared manuscript and its source materials remain in the person's Edison instance until the person explicitly deletes them; the system never purges assets by age, unread status or storage policy.
- **Knowledge asset**: A record owned by the person within their Edison instance, derived from a selected YouTube video, containing the source identity, timed transcript, deep-reading manuscript, generation record and reading state.
- **Acquisition**: Bringing a selected YouTube video's metadata and transcript into the library of the person's Edison instance.
- **Enrichment**: Optional AI-assisted transformations of a knowledge asset, such as cleaning, translating, structuring, summarizing, or indexing. Enrichment never replaces the source transcript.
- **Deep-reading manuscript**: The primary user-facing enrichment of a selected knowledge asset: a coherent, layered document designed for sustained reading and thought, rather than a raw transcript or a summary card. It faithfully removes spoken redundancy, translates and structures the source without adding facts or opinions. It is presented in Simplified Chinese by default, translating non-Chinese sources automatically.
- **Source trace**: The preserved link between a deep-reading manuscript and its original video and timed transcript, allowing the reader to verify a passage on demand.
- **Manuscript version**: An immutable deep-reading manuscript produced for one knowledge asset. A person may manually create a later version while retaining earlier versions for comparison and rollback.
- **Generation record**: The retained account of how a manuscript was produced, distinct from its source materials and current manuscript output.
- **Edison instance（Edison 实例）**: The single running Edison that holds all of one person's durable assets and does their continuous work on a server the person controls, reached from any of their devices through a browser. There is exactly one instance per person; devices are views onto it, never separate copies. _Avoid_: Desktop app, local install, cloud service, account.
- **Self-hosted workspace（自托管资料库）**: The knowledge store inside the person's Edison instance. No product-operated cloud copy or synchronization service exists; all durable assets remain on the server the person controls. _Avoid_: Local-only workspace, per-device library, sync.
- **Credential boundary**: Credentials belong to connection or model settings and remain outside document assets and exports. A document does not own or carry credentials.
- **Workspace**: The browser-facing environment of the person's Edison instance in which a person monitors subscriptions, prepares manuscript batches, chooses a reading queue, and reads or annotates knowledge assets.
- **Migration boundary**: The smallest self-contained YouTube capability that can move from Vegapunk into this product without importing unrelated research-agent behavior.
- **Preparation state**: The operational state of a discovered update: discovered, queued, awaiting classification, acquiring, generating, ready, unavailable, failed, cancelled or excluded as Shorts. Uncertain and excluded videos remain visible in preparation progress; no manuscript is fabricated without a source transcript.
- **Preparation cancellation（取消处理）**: A person's persistent withdrawal of a video from work that has not started. Its existing materials remain preserved, and automatic updates do not return it to the preparation queue. _Avoid_: Deletion, pause, failure.
- **Preparation restoration（恢复处理）**: A person's explicit return of a cancelled video to the preparation queue, subject to existing pause and channel-exclusion rules. Restoration does not promise immediate execution. _Avoid_: Automatic retry, regeneration.
- **Deletion**: An explicit terminal action that permanently removes the entire video asset from the Edison instance, including every manuscript version, transcript and source record.
- **Markdown handoff**: The workbench does not embed a document reader. Each prepared manuscript is stored as Markdown and can be copied as raw Markdown, viewed in the browser, or downloaded to the reading device.
- **Behavioral port**: A clean reimplementation of a verified upstream capability that preserves its externally meaningful protocol behavior, failure semantics, test cases and required attribution without importing its host architecture.
- **Preparation progress（处理进度）**: The operational view of YouTube manuscript preparation, covering queued, active, paused, completed, cancelled, unavailable and failed work. It is distinct from the reading library, which organizes prepared documents for reading. _Avoid_: Activity center, Quiet pulse activity center.
- **Automatic update pause（自动更新暂停）**: The off state of Auto Update, stopping further subscription scans and automatic task starts while current work finishes. Accepted manual requests retain their independent execution permission. _Avoid_: Global stop, cancellation, loss of completion eligibility.
- **Sidecar capability**: A typed user-intent boundary through which the desktop host requests work from the Python core without direct access to the workspace database, yt-dlp or Automic Vault.
- **Default retrieval scope**: Search titles, channel identities and Markdown manuscript text. Timed raw transcripts remain preserved but enter a query only when the person explicitly includes them.
- **Library facets**: Reading state, channel, publication time and preparation state are the complete first-release filter set.
- **Search result context**: Every result exposes channel, publication time, reading state, preparation state and manuscript version before it is selected.

## Translation language

**Translation**:
A rendering of source speech in another language that preserves its meaning, stance, qualifications and named concepts. Translation quality concerns fidelity as well as fluent expression.
_Avoid_: Summary, expansion

**Translation prompt**:
The user's editable instructions for producing or reviewing a translation. These express translation requirements, distinct from selecting the model that carries them out.
_Avoid_: Model configuration

**Complete translation**:
The full rendering of the source speech in the target language, retained as the reference against which a reading manuscript can be checked. It preserves the source's argument and sequence rather than reducing it to a summary.
_Avoid_: Summary, reading manuscript

**Manuscript composition**:
The limited editorial transformation of a complete translation into a reading document: removing spoken filler and adding hierarchical headings while retaining meaning and argument order.
_Avoid_: Summarization, free rewriting

## Object memory language

**items**:
The named module within Edison for the Object Memory System. The original standalone Minimalism application is its migration source.
_Avoid_: Skills manager, a second independently maintained product

**Object Collection（物品集合）**:
A named grouping of belongings. Removing a collection leaves its objects unfiled rather than deleting them.
_Avoid_: Reading queue, destructive folder

**Archived Object（归档物品）**:
A preserved belonging outside the active gallery, held in the protected Archive collection. Unarchiving returns it to Unfiled.
_Avoid_: Deleted object, trash

**Object Trash（物品回收站）**:
The recoverable set of deleted object records, distinct from Archive. Permanent deletion removes a record and its owned media from Edison's current object library.
_Avoid_: Archive, automatic retention policy

**Object Memory System（物品记忆系统）**:
A personal archive of belongings, preserving objects, collections, documentary photos, memory text, archived belongings and AI-generated covers. It is a distinct domain within Edison, separate from Skills management.
_Avoid_: Inventory accounting, Skill library

**Archive Photo（档案照片）**:
A user-provided photograph documenting the real object, distinct from an AI-generated representation.
_Avoid_: AI cover, generated evidence

**AI Memory Cover（AI 记忆封面）**:
A generated visual representation of an object used as its cover, distinct from documentary photographs of the real object.
_Avoid_: Archive photo, factual photographic evidence

## Focus language

**minimalism**:
The module that presents one current task, advancing directly to the next when it is completed. Capture and priority ordering are available on demand.
_Avoid_: Object memory, task dashboard

**Current task（当前事项）**:
The first task in the ordered queue, receiving the person's attention now. New captures append to the queue without replacing it.
_Avoid_: Parallel task, focus mode toggle

## Curriculum language

**Curriculum（课程周览）**:
The person's weekly view of university courses, combining their timetable, academic calendar, and class period times. Personal tasks and schedule editing are outside this module's scope.
_Avoid_: Task planner, schedule editor

**Course occurrence（一次课程安排）**:
A course scheduled on a particular date and time, with its assigned classroom and teacher. Separate occurrences of the same course may have different rooms or teachers.
_Avoid_: Course definition, personal task

**Course-free interval（无课时段）**:
A gap between known course commitments within a covered timetable period. It does not establish availability for other commitments.
_Avoid_: Guaranteed availability, missing timetable

**Course notification（课程通知）**:
A system notification marking the scheduled start or end of one numbered class period. It belongs to the operating system's notification surface, distinct from messages inside Edison.
_Avoid_: In-app toast, personal task reminder

**Class period（课程节次）**:
One numbered teaching interval in the university's daily timetable, with its own start and end time. A single course occurrence can contain several periods separated by breaks.
_Avoid_: Entire course occurrence, weekly course
