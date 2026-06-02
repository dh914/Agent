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

## GitHub OTP 자동 추출 (수동 트리거)

GitHub 인증 메일에서 6–8자리 코드 또는 디바이스 인증 링크를 꺼내오는
별도 워크플로우입니다. **스케줄러 없이 `workflow_dispatch` 만** 허용하며,
실행자(`github.actor`)가 저장소 소유자와 일치할 때만 동작합니다.

- 스크립트: `scripts/github_otp.py`
- 워크플로우: `.github/workflows/github-otp.yml`
- 실행: **Actions → Fetch GitHub OTP → Run workflow**

코드는 step output(`steps.otp.outputs.code`)으로만 전달되며 로그에는
마스킹된 값(`4****3`)만 남습니다. 후속 단계에서 GitHub API 호출이나
Playwright 입력에 사용할 수 있도록 step output 으로 노출합니다.

### iCloud 앱 암호 발급 (한 번만)

1. https://account.apple.com → **로그인 및 보안 → 앱 암호**
2. 새 앱 암호 생성(라벨 예: `agent-imap`)
3. 표시되는 16자리 암호를 GitHub Secret 으로 등록:
   - `IMAP_HOST` = `imap.mail.me.com`
   - `IMAP_USER` = `<Apple ID>@icloud.com`
   - `IMAP_PASS` = 발급받은 앱 암호 (공백 포함 그대로)

앱 암호는 IMAP 전용으로 별도 발급되며, 분실/유출 시 같은 화면에서
즉시 폐기할 수 있습니다. 절대로 코드, 커밋, 이슈, 로그에 평문으로
남기지 마세요.
