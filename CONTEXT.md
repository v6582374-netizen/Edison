# Edison

Edison's product language covers the YouTube reading workbench, Curriculum and the independent EPUB Reader.

minimalism (the focus module) has been removed. items (object memory) is retained only in the ignored `.local/items/` archive; it has no Web entry, settings, API or release payload. See ADR 0015.

## YouTube reading language

- **Continuous YouTube availability（YouTube 持续可用）**: After initial connection, Edison continues its authorized background work through ordinary credential expiry, closed browser pages and service restarts without the person manually moving credentials. Google revocation or an explicit security challenge may require the person to sign in again; this does not promise access to every video. _Avoid_: Permanent credentials, never needing verification, connected account equals available captions.

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
- **YouTube cooldown（YouTube 冷却）**: A shared waiting period imposed after YouTube limits requests. It suspends further subtitle and related content acquisition while preserving independent official API discovery and existing reading assets, and survives restart or manual queue changes.
- **Rate-limit waiting（限流等待）**: A video's recoverable preparation state while platform access is restricted. Shared, low-frequency recovery checks can resume eligible work after cooldown without an explicit retry; waiting does not override cancellation or execution permission. _Avoid_: Exhausted video budget, permission to bypass cooldown.
- **Capability recovery check（能力恢复探测）**: A bounded check of an interrupted service capability shared by its waiting work, rather than another attempt for every affected video. Success permits eligible work to resume but does not restore cancelled tasks or disconnected identities. _Avoid_: Unlimited video retries, browser restart on every error.
- **Caption availability waiting（字幕可用性等待）**: A bounded wait for one accessible video's source captions after service access has been established and network, throttling and authentication barriers have been ruled out. An empty acquisition result alone does not establish this state. _Avoid_: Login challenge, service outage, proof that captions will never exist.
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
- **Edison sign-in（Edison 登录）**: The person's authenticated access to their Edison workspace, distinct from permission for Edison to access Google or YouTube. _Avoid_: YouTube connection, background-work permission.
- **Google authorization（Google 官方授权）**: The person's grant for Edison to read their YouTube subscriptions and permitted official video information. It does not establish a YouTube content session or permission to download every video's captions. _Avoid_: Edison sign-in, caption availability.
- **YouTube content session（YouTube 内容会话）**: The person's signed-in YouTube context used by Edison to acquire source content within its supported scope. It is distinct from Google authorization, even when both use the same Google account. _Avoid_: Subscription authorization, guaranteed caption access.
- **YouTube account consistency（YouTube 账号一致性）**: The person's confirmation that Google authorization and the YouTube content session use the same Google account. Edison does not independently verify this identity match; a YouTube channel identity is not a substitute. _Avoid_: Automatically verified account match, dedicated profile means separate account.
- **YouTube disconnection（断开 YouTube）**: The person's withdrawal of both YouTube connections from their Edison instance, ending their use and clearing their stored credentials while retaining existing knowledge assets. Reconnection requires explicit intent, and local removal does not establish successful revocation at Google. _Avoid_: Edison logout, subscription-only disconnection, asset deletion.
- **YouTube cutover hold（YouTube 切换暂停）**: A temporary maintenance barrier against new YouTube work and competing connection changes while the instance changes its acquisition path. It preserves the person's Auto Update choice and accepted manual requests. _Avoid_: User cancellation, turning Auto Update off, proof that active work has finished.
- **Edison code rollback（Edison 程序回退）**: Returning the instance to a compatible earlier program while retaining its latest knowledge assets, reading records and user permissions; acquisition may remain stopped. It does not restore old credentials or rewind the workspace to an earlier snapshot. _Avoid_: Whole-library restore, automatic return to the old acquisition path.
- **Capability-specific interruption（按能力中断）**: An interruption that prevents only work depending on the unavailable capability; independent discovery, translation or reading can continue. Restored availability does not override task eligibility, cancellation or the person's execution permissions. _Avoid_: Entire workspace outage, automatic restoration of cancelled work.
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

**Classroom source transcript（课堂原始字幕）**:
The preserved, unedited transcription supplied by the classroom recording platform, including its available timestamps. It is source material for a classroom document and does not establish that every spoken passage was captured.
_Avoid_: Classroom document, corrected transcript, complete classroom record

**Classroom document（课堂整理文档）**:
A reading document organized from source material for a platform-recorded class. It can be an edited transcript or detailed revision notes; neither replaces its source transcript.
_Avoid_: Raw subtitles, course definition

**Edited classroom transcript（字幕整理稿）**:
A lightly edited, time-referenced rendering of platform captions. It may remove meaningless repetitions and fillers, correct clear typos and improve punctuation and paragraphing, while preserving substantive content, language and order without adding explanations.
_Avoid_: Summary, expanded lecture, guaranteed complete speech record

**Classroom backup（课堂本地备份）**:
A retained copy of the same reader-visible classroom document versions on the person's device. A server archive and a device backup can be current at different times.
_Avoid_: Another Edison instance, browser cache, Git push

**Recorded class（录播课次）**:
A classroom session identified and listed by the classroom recording platform. It is the unit of classroom document archiving, distinct from a scheduled course occurrence or a single numbered class period.
_Avoid_: Timetable occurrence, class period, proof of complete recording

**Supplementary classroom transcript（补充转写）**:
A transcription obtained from the recording audio to supplement absent or confirmed incomplete platform subtitles. Its source remains distinct from the preserved platform transcript.
_Avoid_: Original platform subtitles, inferred missing speech

**Course archive（课程档案）**:
The Curriculum collection of classroom documents, grouped by course by default. Source transcripts are retained separately from its reader-facing documents.
_Avoid_: Object Archive, deleted courses


## Learning Gather language

**Learning Gather（学习 Agent 空间）**:
The person's conversations with learning agents around their current core goals, including group conversations and private conversations.
_Avoid_: Human social network, general activity feed

**Core learning goal（核心学习目标）**:
A learning outcome deliberately selected by the person to guide agents' assistance. The goal is distinct from individual practice prompts and any one conversation.
_Avoid_: Reminder schedule, chat topic

**Proactive learning message（主动学习消息）**:
A message initiated by a learning agent to help the person progress toward a core learning goal. Its presence in a conversation is distinct from a notification outside Edison.
_Avoid_: External notification, proof of learning progress

**Learning notification（学习通知）**:
An optional notice outside the learning conversation that draws the person's attention to a learning message. It is independent of whether agents may create messages inside Edison.
_Avoid_: Learning message, required reply

## Reader language

**Reader（阅读器）**:
The person's independent reading module for deliberately imported EPUB books, entered from Edison navigation and occupying its main content area. Its books and reading records belong to the person's Edison instance rather than a required Readwise account.
_Avoid_: Readwise client, narrow browser sidebar, YouTube workbench

**Reader book（阅读器书籍）**:
An EPUB deliberately imported into Reader, together with its book identity and reading records. It is distinct from a generated YouTube manuscript or a classroom document.
_Avoid_: Knowledge asset, classroom document, rendered page

**Reader location（阅读器归类）**:
The person's placement of a book in Inbox, Later or Archive. Placement expresses attention and organization, independently of the point reached in its text.
_Avoid_: Reading position, deletion, YouTube reading state

**Reading position（阅读位置）**:
The place in a book from which the person can resume reading. It remains meaningful when the text is displayed with a different font size or window width.
_Avoid_: Scroll percentage, Reader location, fixed screen page

**Reader progress（阅读器进度）**:
The furthest text position the person has reached through navigation. It never decreases when they revisit earlier text, and changing the layout does not advance it. A deliberate jump counts as reaching that location, not proof that the intervening text was read or understood. It is independent of the current stopping position.
_Avoid_: Current scroll offset, Reader location, proof of comprehension

**Reader highlight（阅读器高亮）**:
A passage selected by the person and retained in association with its source text in a book. Changes to its display do not change which passage was selected.
_Avoid_: Screenshot, copied quotation without a source

**Reader note（阅读器笔记）**:
The person's commentary associated with a book or a highlighted passage. It remains distinct from the source text.
_Avoid_: Rewritten source, generated summary


**Reader Notebook（阅读器笔记本）**:
The collection of a book's highlighted passages and the person's notes about the book or those passages. Returning to a quotation leads to its original context, while a passage that can no longer be verified remains visible as needing repair rather than being silently relocated.
_Avoid_: Source text, reading position, generated summary


**Reader search result（阅读器查找结果）**:
A matching passage or saved note associated with its book and original context. Following a result must identify that context rather than merely find similar words. A result is not itself a highlight or evidence of having read the passage.
_Avoid_: New annotation, proof of reading, percentage location
