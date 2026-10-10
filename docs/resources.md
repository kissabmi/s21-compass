# Resource and UI matrix

Official current source (checked 2026-10-10):
https://platform.21-school.ru/services/21-school/api/swagger — 25 paths, 25 GET
operations, 41 schemas. Structurally matches the pinned community mirror
`ikaelfess/school21_api_sdk`, commit `57bab5d98984aa5a147624de68a5bae734bd1f4a`,
`openapi.yaml`. Paths below append to
`https://platform.21-school.ru/services/21-school/api/v1`; **all are GET**.
L/O = limit 1..1000 / offset >=0. Responses have no total or cursor. Next is
possible when len(items)==limit; next page may be empty. Filter change resets offset.

| Resource | Query | Shape / key fields | Both TUI and web |
| --- | --- | --- | --- |
| participants/{login} | none | login,level,expValue,expToNextLevel,campus{id,shortName},status | Overview, peer lookup by exact nickname |
| participants/{login}/points | none | peerReviewPoints,codeReviewPoints,coins | Overview |
| participants/{login}/logtime | date optional YYYY-MM-DD | number (average weekly hours) | Overview, optional date |
| participants/{login}/feedback | none | averageVerifierPunctuality/Interest/Thoroughness/Friendliness optional nullable | Overview |
| participants/{login}/skills | none | skills:[{name,points}] | Progress |
| participants/{login}/badges | none | badges:[{name,receiptDateTime,iconUrl}] | Progress |
| participants/{login}/coalition | none | coalitionId,name,rank optional | Overview, members |
| participants/{login}/workstation | none | clusterId,clusterName,row,number | Overview |
| participants/{login}/experience-history | L/O default 50 | expHistory:[{expValue,accrualDateTime}] | Progress, pages |
| participants/{login}/projects | L/O default 10,status | projects:[{id,title,type,status,finalPercentage?,completionDateTime?,teamMembers?,courseId?}] | Projects, status filter, pages, detail |
| participants/{login}/projects/{projectId} | none | one participant project | Combine with catalog detail |
| projects/{projectId} | none | projectId,title,description,durationHours,type,xp?,startConditions?,courseId? | Detail; percentage can exceed 100 |
| projects/{projectId}/participants | L/O default 50,status,campusId UUID | participants:[login strings] | Detail -> peers list |
| participants/{login}/courses | L/O default 10,status | courses:[{id,title,status,finalPercentage?,completionDateTime?}] | Courses, status filter, pages, detail |
| participants/{login}/courses/{courseId} | none | participant course | Combine with catalog detail |
| courses/{courseId} | none | courseId,title,description,durationHours,xp,startConditions? | Course detail |
| events | from,to REQUIRED UTC ISO; type optional ACTIVITY/EXAM/TEST; L/O default 50 | events:[{id,type,name,location,startDateTime,endDateTime,capacity,registerCount,description?,organizers?}] | Date range, type filter, pages, event detail |
| campuses | none | campuses:[{id UUID,shortName,fullName}] | Campus selection |
| campuses/{campusId}/clusters | none | clusters:[{id,name,capacity,availableCapacity,floor}] | Cluster selection |
| campuses/{campusId}/participants | L/O default 50 | participants:[strings] | Campus peer list, pages, peer selection |
| campuses/{campusId}/coalitions | L/O default 50 | coalitions:[{coalitionId,name}] | Coalition list, pages, selection |
| coalitions/{coalitionId}/participants | L/O default 50 | participants:[strings] | Coalition peers, pages |
| clusters/{clusterId}/map | L/O default 50; occupied optional true/false | clusterMap:[{row,number,login?}] | Conditional seat grid/list, occupation filter, pages |
| graph | none, no login | nodes:[{id,label,items:[{id,code,handles,entityType,entityId}]}],edges:[{id,source,target,sourceHandle,targetHandle}] | Searchable grouped project tree and dependency links; no fictional coordinates |
| sales | none, no login | sales:[{type PRP/CRP,status NON_ACTIVE/ACTIVE/PLANNED,startDateTime?,progressPercentage?}] | Readable status list |

Project statuses: ASSIGNED, REGISTERED, IN_PROGRESS, IN_REVIEWS, ACCEPTED, FAILED.
Course statuses omit IN_REVIEWS. Entity IDs integers; campus UUID; login encoded
as one validated segment. Optional/nullable fields display em dash, never fake zero.
Raw JSON is a supplemental inspector for complex conditions and full detail only.

## UI behavior acceptance

Both interfaces expose the rows above by section navigation and contextual drilldown.
Initial/network load has a visible loading indication, pending auth cannot double
submit. Empty data gets an explicit empty state. Error has a safe explanation and
retry/refresh action; 401 returns login, 403 permission message, 429 mentions wait,
timeout does not look like empty data. Late responses must not replace a newly
selected section or peer. Profile section errors remain partial with useful data.
Both UI have logout, refresh, exact nickname lookup and a persistent demo marker.
TUI includes keyboard guidance; web adapts to 375px and keyboard navigation.

## Delivery acceptance

R-PROJECT is complete only after independently checked source is visible in public
`https://github.com/kissabmi/s21-compass` and remote commit matches local accepted
commit. Owner explicitly approved this delivery on 10.10.2026. Local package alone
is partial. Authenticated read-only smoke succeeded on 2026-10-10; private account
data is excluded from public evidence. Not every upstream resource has data or
returns success for every ID/account.
