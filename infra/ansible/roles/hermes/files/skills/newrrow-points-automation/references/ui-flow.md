# Newrrow UI Flow

Use this reference after the Hermes `newrrow-points-automation` skill triggers.

## Routes

Base: `https://gbsm.newrrow.com`

| Area | Route |
| --- | --- |
| Home/checklist | `/csr-platform/home` |
| Tasks and timetable | `/working-station/tasks` |
| CSR question | `/csr-platform/knowledge/csr-question` |
| Training | `/csr-platform/training/home` |
| Assignment | `/csr-platform/submission/home` |
| Reflection | `/csr-platform/reflection/home` |
| Dashboard | `/csr-platform/dashboard` |

Observed navigation labels: `홈`, `CSR 질문`, `할 일`, `훈련 기본 과정`, `과제`, `대시보드`, `성찰`.

## Preflight

1. On every Newrrow run, use the active Hermes browser/agent-browser session. If no Newrrow tab is available, open the hardcoded home URL with `agent-browser open "https://gbsm.newrrow.com/csr-platform/home"` or the Hermes browser navigation tool. Do not wait for a separate "open the website" request when no Newrrow tab is available.
2. Navigate to `/csr-platform/home` only if the claimed tab is not already there or if a previous workflow left it in a detail page.
3. Snapshot the home page and record today's visible checklist items.
4. Treat text near each card as authoritative. If a card or adjacent status says `완료`, mark that point as already done and do not repeat it.
5. After actions, a cleared home list is also confirmation. If today's date is visible and `오늘 마감` or pending action cards disappear, or `오늘 할 일` has no pending items, do not reopen completed routes just to force visible `완료` cards.
6. Create or update a Hermes `todo` plan before starting point actions. Use visible point checklist items as plan steps.
7. Keep exactly one unresolved action `in_progress` while operating its route. When an item is visibly confirmed, skipped, or blocked, update the `todo` plan immediately and record the precise outcome in the running result table.
8. Keep the running result table in sync with the plan while working.

## User Intervention Required

The user has preapproved the defaults below. Do not pause for those actions unless the UI state is ambiguous or blocked.

| Action | Preapproved default |
| --- | --- |
| Reflection comment | Write any short supportive comment on any visible shared reflection, regardless of date. |
| Reflection share | Share with `김예범 / 1-2`. |
| Gratitude card | Send to `김예범 / 1-2`, category `친구`, message `.`. |
| Short personal content | Use very brief content such as `클컴 공부`, `자바 공부`, or `헬스`. |
| Training or weekly score | Write the strongest honest answers possible and aim for `5`; choose the highest score the completed answers can support. |
| Login | Use the 1Password-backed login helper (`scripts/newrrow-login.sh`) or `op read` references from `NEWRROW_USERNAME_REF` and `NEWRROW_PASSWORD_REF` without printing secrets. |

Only pause and show this table when one of these cases occurs:

| Situation | Why user input is needed | What to ask for |
| --- | --- | --- |
| Assignment needs a file/text that is not available on the website | The user said to intervene only when a required assignment file/content is missing from the website. | The missing file/text, or approval to skip the assignment. |
| 1Password or Newrrow login requires user action | Missing 1Password item access, 2FA, account chooser, or security prompt may require the user. | Complete the visible Newrrow/1Password step, fix the configured secret reference, or say whether to skip. |
| CAPTCHA or browser permission prompt appears | Browser safety/account state requires explicit user action. | Complete the CAPTCHA or permission decision in agent-browser. |
| Recipient search is ambiguous | There may be multiple `김예범 / 1-2` matches or no visible match. | Which visible recipient to choose, or whether to skip. |
| Website blocks submission or required controls stay disabled | The UI state prevents honest completion. | Whether to retry, skip, or provide missing details. |

## Point Checklist

| Item | Points | Primary route | Completion strategy |
| --- | ---: | --- | --- |
| `[할일] 하루 동안 할일 1번 이상 등록` | 15 | `/working-station/tasks` | Add one low-impact today task if no today task was created. |
| `[할일] 하루 동안 타임테이블 1번 이상 등록` | 5 | `/working-station/tasks` | Add one short timetable block in an empty slot. |
| `[훈련] 지정 훈련 1개 완료` | 25 | home or training | Complete the `[훈련] ...` item shown on home unless already complete. |
| `[훈련] 하루 동안 자율 훈련 1개 이상 완료` | 25 | `/csr-platform/training/home` | Complete one recommended training not already completed today. |
| `[훈련] 하루 동안 최소 1번 훈련 점수 4점 이상` | 50 | training completion | Write strong answers and aim for 5; use the highest score the answers support. |
| `[회고] 하루 동안 회고 1번 이상 작성` | 25 | `/csr-platform/reflection/home` | Complete today's daily reflection or skip if complete. |
| `[회고] 일주일 동안 주간 회고 1번 이상 완료` | 50 | reflection weekly | Generate and complete the weekly reflection. |
| `[회고] 주간 회고 점수 4점 이상` | 50 | reflection weekly | Write strong weekly reflection content and aim for 5. |
| `[과제] 과제 1개 완료` | 25 | home or `/csr-platform/submission/home` | Auto-submit when the needed content/file is already on the website; ask only if it is missing. |
| `[지식] 하루 동안 CSR 질문 1번 이상 생성` | 20 | `/csr-platform/knowledge/csr-question` | Click one visible suggested question button; type a fallback question only if no suggestion is visible. |
| `[상호작용] 하루 동안 감사카드 1번 이상 전송` | 15 | daily reflection completion page | Send to `김예범 / 1-2`, category `친구`, message `.`. |
| `[상호작용] 하루 동안 회고 댓글 1개 이상 작성` | 5 | reflection feed | Post any short supportive comment on any visible shared reflection. |
| `[상호작용] 하루 동안 회고 1번 이상 공유` | 5 | daily reflection completion page | Share with `김예범 / 1-2`. |
| `[습관] 하루 동안 실천목표 1개 이상 등록` | 10 | daily reflection completion page | Add one realistic practice goal. |
| `[모니터링] 하루 동안 대시보드 1번 이상 조회` | 5 | `/csr-platform/dashboard` | Open dashboard and wait for ranking/points table. |

## Task and Timetable

Route: `/working-station/tasks`.

Observed task controls:
- `할 일 추가` opens an inline task form with textbox `입력하세요.`, buttons `취소`, `추가`.
- Bottom quick-entry textbox placeholder: `업무를 등록해 주세요.`, with button `추가`.
- Existing task rows include name, `완료`, date, priority like `보통`, and buttons such as `등록 완료` or `성과 등록`.

Task registration:
1. Check whether a task dated today already exists; if yes, mark already done.
2. Otherwise add `클컴 공부`, `자바 공부`, or `뉴로우 포인트 점검 YYYY-MM-DD`.
3. Prefer the bottom quick-entry textbox `업무를 등록해 주세요.` if the inline `할 일 추가` form opens but submit produces no visible row; filling the quick-entry and clicking its enabled `추가` was verified to create a visible row such as `뉴로우 포인트 점검 대기 미지정 보통 성과 등록`.
4. Verify the new row appears near the top of `나의 할 일 목록`.

Timetable registration:
1. On `/working-station/tasks`, find an empty `시간 슬롯 HH:MM AM/PM` button in the visible timetable.
2. Click it. The expected modal/article contains `업무명`, textbox `입력하세요`, priority `보통`, `하루 종일`, start/end time boxes, `취소`, `저장`.
3. If `agent-browser click @ref` on a visible `시간 슬롯` does not open the modal, use the screenshot/box coordinates for the empty gap and click the center with `agent-browser mouse move <x> <y> && agent-browser mouse down && agent-browser mouse up`. This was verified to open the timetable modal for an empty `01:10 PM` gap where direct ref-clicks were no-ops.
4. Enter `클컴 공부`, `자바 공부`, or `뉴로우 포인트 점검`.
5. Keep the default 30-minute range unless the user specified a time.
6. Click `저장` only after the `저장` button is enabled.
7. Verify a timetable block with the entered name appears.
8. Pitfall: the home-page `일정 생성` button can open a `면담` modal, not a timetable block editor. Do not use that as the timetable path; cancel it and return to `/working-station/tasks`.
9. Only record timetable as blocked/skipped after both ref-click and coordinate-click fail.

## CSR Question

Route: `/csr-platform/knowledge/csr-question`.

Observed controls:
- `새 대화`
- `대화 이력`
- Suggested question buttons near the input; prefer these over typing a custom default.
- Textarea placeholder: `여러분의 질문을 기다리고 있어요!`
- Send button is icon-only.
- The route can briefly render only navigation plus an empty `list`. Wait briefly and take a fresh snapshot before treating CSR controls as missing.

Default behavior if the user did not provide a question:
1. Click the first visible suggested question button.
2. Do not type a custom default while suggested question buttons are visible.
3. If no suggested question button is visible, type this fallback question:
`CSR을 일상 업무 계획과 회고에 꾸준히 연결하는 좋은 방법은 무엇인가요?`

After clicking a suggestion or sending the fallback, verify the question appears in the conversation or the answer starts streaming. If the question is visible and either an answer paragraph appears or `생성 중지` is visible, mark CSR question done; do not wait for the full answer to finish streaming.

## Training

Route: `/csr-platform/training/home`.

Observed controls:
- Filters: `전체`, `대화하기`, `전략 세우기`, `협업하기`, `성찰하기`
- Search placeholder: `훈련명 검색`
- `완료 이력 보기`
- `오늘 해야할 훈련이 마무리 되었어요! 고생하셨습니다!` means today's planned training is complete.
- Example cards:
  - `전략 도구 활용하기: 아이젠하워 매트릭스`
  - `갈등 조정과 합의 이끌어내기`
  - `다른 사람의 작업물에 대해 피드백하기`
  - `불편한 이야기를 지혜롭게 전달하기`
  - `나만의 소통 훈련 만들기`

Designated training:
1. On home, look for the visible `[훈련] ...` card under today's checklist.
2. If adjacent text says `완료`, mark it already done.
3. If not complete, open that card or search its exact title in training.
4. If the training home says today's training is complete, mark the designated training already done and do not start another execution.

Self-directed training:
1. Prefer a simple recommended card that is not marked complete today.
2. If available, `전략 도구 활용하기: 아이젠하워 매트릭스` is acceptable.
3. Continue any current URL with `trainingExecutionId` rather than starting another duplicate execution.
4. A `완료 이력 보기` entry dated today, or a scenario detail showing `1회 완료`, is valid evidence that a training was completed today.

Observed training detail for `아이젠하워 매트릭스`:
- Steps: `준비하기`, `개념 이해하기`, `실전 훈련하기`, `리뷰하기`, `정리하기`
- Scenario selection page has a `다음` button.
- Concept step can include drag classification and `정답 확인`; `다음` remains disabled until the required interaction is correct.
- Drag/drop cards may expose React DnD attributes such as `data-rbd-draggable-id` and empty target slots with `data-rbd-droppable-id`. Drop onto the empty `정답을 여기에 드래그해서 옮겨 주세요.` slot, not just the larger category box; verify the live status or target text changes before clicking `정답 확인`.
- In strategy-edit steps, clicking `변경하기` enables one textarea and disables the other `변경하기` buttons. Fill that one textarea, click the dark icon-only save button next to it, then edit the next section. After all needed edits are saved, click `피드백 받기`.

Training rules:
- Read the scenario, answer the exercise genuinely, and use the page's feedback.
- Do not skip by guessing final URLs.
- If drag/drop is required, use visible card labels and target category text.
- For self-rating or score prompts, improve the written answers first and aim for 5; use the highest score the completed answers can support.
- Verify final completion by a completion screen, completed history, home `완료`, or dashboard point update.

## Reflection, Habit, Gratitude, Share

Route: `/csr-platform/reflection/home`.

Observed home controls:
- `아직 오늘의 회고를 작성하지 않았어요...`
- `일일 회고 하루를 되돌아보고 배운 점과 경험을 정리하기`
- `주간 회고 일주일의 경험을 돌아보고 계획을 정리하기`
- `감사 카드 보관함 일상 속 감사를 상대와 주고받기`
- `피드백 보관함 내 계획과 회고에 대한 피드백 확인하기`

Daily reflection:
1. Click the daily reflection card or the "not written" prompt.
2. If a date modal appears, choose today's date and `확인`.
3. If the home prompt says `오늘의 회고 작성을 완료하셨어요!` or the completion page appears with `일일 회고를 모두 작성하셨어요`, mark daily reflection done.
4. Otherwise answer prompts with very brief, non-sensitive content such as `클컴 공부`, `자바 공부`, or a one-sentence version of those activities.
5. When the daily reflection flow offers optional deeper prompts such as `더 생각해 보기`, `추가 질문`, question selection, or a `넘어갈게요` button, skip them instead of answering unless the user explicitly asks for deeper reflection content.
6. Submit only after required fields are filled and verify the completion page.

Observed daily completion actions:
- `실천 목표 세우기` opens `실천 목표 만들기` with textbox placeholder `실천 목표를 입력해주세요 (예시: 친구의 입장에서 먼저 생각하기)`.
- `회고를 작성하며 감사한 대상이 떠올랐나요?` opens `감사 카드 작성하기`.
- `작성한 일일 회고를 공유해보세요.` opens `회고 공유`.
- `내용 수정하기`
- `나가기`

Practice goal:
- Default goal: `내일은 클컴 공부나 자바 공부 중 하나를 먼저 끝내기`.
- Click `실천 목표 세우기`, fill the textbox, click `확인`, and verify it closes or appears as registered.

Gratitude card:
- Categories include `선생님`, `친구`, `자신`, `기회`, `학급`, `오늘`, `건강`, `더 보기`.
- Select category `친구`.
- Search/select recipient `김예범 / 1-2`.
- Message: `.`
- Send without asking again unless there are multiple ambiguous `김예범 / 1-2` matches or no match.

Reflection share:
- Share modal may show existing or previous recipients, for example `김예범 / 1-2`, and buttons `직전 공유 대상자 불러오기`, `다음에 할게요`, `공유하기`.
- Add/select `김예범 / 1-2` and click `공유하기` without asking again unless the recipient match is ambiguous.
- If a generated weekly report or visible activity summary says `일일 회고를 1명에게 공유했어요`, mark reflection sharing already done and do not share again.

Reflection comment:
- Use the reflection feed (`회고 피드`) and choose a visible shared reflection, not the user's own reflection.
- Date does not matter.
- Draft and post any short supportive comment, such as `좋은 회고네요.` or `응원합니다.`, without asking again.
- Reflection detail drawers use a rich-text `role="textbox"` for comments. Fill that textbox directly, then submit only when the `등록` button is enabled; if a coordinate click does not enable `등록`, re-target the textbox by role/placeholder and verify the drafted comment is visible.

Already-done social actions:
- If daily reflection was completed earlier and its detail page only shows `회고 수정` plus comment controls, do not assume gratitude/share/practice buttons are still available there.
- Use visible weekly report counters or activity summaries as confirmation. If they show `감사 카드를 1명에게 보냈어요` or `일일 회고를 1명에게 공유했어요`, mark those items already done and avoid duplicate sends/shares.

Weekly reflection:
1. Click `주간 회고`.
2. If it shows `주간회고 생성`, review `반영되는 회고`.
3. If only a small number of daily reflections are available, proceed anyway unless the UI blocks completion.
4. Click `주간 회고 리포트 받기`, complete follow-up prompts, and click `회고 완료` when enabled.
5. If the weekly report is already generated and a response textbox is prefilled with `회고 완료` enabled, click `회고 완료`; returning to the reflection home confirms completion.
6. Improve the written weekly reflection content and aim for 5; use the highest score the completed content can support.
7. Do not treat a report level such as `Lv.3` as the same thing as the point checklist's `4+` score unless the UI explicitly presents it as that score. If the score is not separately visible, verify completion through the weekly report, home checklist, or dashboard and report the score item with that evidence.

## Assignments

Routes: home checklist or `/csr-platform/submission/home`.

Observed submission page:
- Heading `과제 제출`
- Tabs `진행`, `마감`
- Search placeholder `과제명, 캠페인 명 검색`
- Table columns `과제명`, `제출 기한`, `남은 기간`, `제출 여부`, `과제 보기`, `다운로드`

Rules:
- If home shows the assignment card with adjacent `완료`, mark already done.
- If `/csr-platform/submission/home` on the `진행` tab says `컨텐츠가 없습니다.` or `총 0개`, and the home assignment card was already complete or no longer pending, mark assignment already done/no current submission.
- If a required file or answer is already available on the website, use it and submit automatically.
- If the assignment requires a file/text that is not available on the website, ask the user for that missing content.
- Do not upload arbitrary local files or invent a required external artifact.
- If the assignment is a simple text response, use brief content such as `클컴 공부` or `자바 공부` when appropriate, submit, and verify `제출 완료` or equivalent status.

## Login

If Newrrow asks for login:

1. Prefer the installed helper: `bash "$HERMES_HOME/skills/productivity/newrrow-points-automation/scripts/newrrow-login.sh"`. It reads `NEWRROW_USERNAME_REF` and `NEWRROW_PASSWORD_REF` with `op read`, stores the credentials only in agent-browser's temporary auth profile via `--password-stdin`, logs in, then deletes that temporary auth profile.
2. If operating manually, read the username/password from 1Password references only long enough to fill the visible Newrrow login form. Do not print, echo, persist, or include secret values in the final response.
3. Do not inspect cookies, local storage, browser profile files, or hidden credential stores.
4. If 1Password cannot read the configured refs, if Newrrow requires 2FA, or if a CAPTCHA/security prompt appears, stop and ask the user to complete that visible step or update the refs.

## Dashboard

Route: `/csr-platform/dashboard`.

Observed dashboard:
- Tab `포인트`
- Period buttons `주간`, `월간`, `누적`
- Ranking and points table including the user's row.
- Sometimes the table body says `구성원이 없습니다.` while ranking summary numbers are still visible. Treat dashboard 조회 as done when the dashboard route loads and visible ranking/point summary text appears.

Open the dashboard once after the other actions. Wait until the ranking/points table is visible. Record the user's visible points if present.

## Final Checklist Template

Use this shape in the final response:

```text
완료:
- [done/already done/skipped/blocked] 할일 등록
- [done/already done/skipped/blocked] 타임테이블 등록
- [done/already done/skipped/blocked] 지정 훈련
- [done/already done/skipped/blocked] 자율 훈련
- [done/already done/skipped/blocked] 훈련 점수 4+
- [done/already done/skipped/blocked] 일일 회고
- [done/already done/skipped/blocked] 주간 회고
- [done/already done/skipped/blocked] 주간 회고 점수 4+
- [done/already done/skipped/blocked] 과제
- [done/already done/skipped/blocked] CSR 질문
- [done/already done/skipped/blocked] 감사카드
- [done/already done/skipped/blocked] 회고 댓글
- [done/already done/skipped/blocked] 회고 공유
- [done/already done/skipped/blocked] 실천목표
- [done/already done/skipped/blocked] 대시보드 조회
```
