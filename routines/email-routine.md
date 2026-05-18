# Email Routine (hourly)

매 정시(`cron: 0 * * * *`)에 GitHub Actions 가 자동 실행하는 메일 처리 사이클입니다.

## 흐름

1. **메일 확인** — Agent 저장소의 `scripts/email_routine.py` 가 IMAP 으로 미열람 메일을 가져옵니다.
2. **회신 및 협업 진행**
   - 본문/제목에 데이터·분석 키워드가 포함되면 **Data 저장소**(`dh914/Data`) 에 `data-request` 이슈를 자동 등록합니다.
   - 발신인에게 접수/처리 안내 메일을 SMTP 로 회신합니다.
3. **기록**
   - Agent 저장소 `logs/<UTC-timestamp>.json` 에 사이클 결과를 커밋합니다.
   - **System 저장소**(`dh914/System`) 에 사이클 요약 이슈(`email-routine`, `audit` 라벨)를 생성하여 단일 감사 추적점을 유지합니다.

## 저장소 역할

| 저장소 | 역할 |
| --- | --- |
| `dh914/Agent` | 메일 fetch/회신/로그 작성 (이 저장소) |
| `dh914/Data` | 메일에서 파생된 데이터 요청 수행 (이슈로 접수) |
| `dh914/System` | 모든 사이클의 오케스트레이션·감사 기록 보관 |

## 필요한 GitHub Secrets

| 이름 | 용도 |
| --- | --- |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASS` | 받은편지함 조회 |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS` | 회신 발송 (SMTPS 465) |
| `CROSS_REPO_TOKEN` | Data/System 저장소 이슈 생성 및 체크아웃 권한을 가진 PAT |

자격 정보가 비어 있어도 워크플로우는 실패하지 않고 해당 단계만 건너뜁니다 — 빈 환경에서도 안전하게 dry-run 됩니다.

## 수동 실행

GitHub UI 의 **Actions → Hourly Email Routine → Run workflow** 또는

```
gh workflow run "Hourly Email Routine" -R dh914/Agent
```
