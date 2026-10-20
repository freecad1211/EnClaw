# EnClaw Ubuntu 개발 환경 설정 가이드

이 문서는 EnClaw 개발 브랜치를 내려받아 테스트하고, 프로젝트 정책과 작업 큐를 설정하기 위한 가이드입니다. 운영 배포 가이드가 아닙니다. 현재 OS/컨테이너 샌드박스와 운영 수준의 보안 제어는 완성되지 않았으므로 신뢰할 수 없는 코드를 실행하지 마세요.

## 1. 개발 브랜치 내려받기

\`\`\`bash
git clone --branch feature/project-policy-engine https://github.com/freecad1211/EnClaw.git
cd EnClaw
python3 --version
git --version
\`\`\`

Python 3.11 이상과 Git 2.30 이상을 사용합니다. 이 절차는 기존 OpenCode, Forge, NanoClaw 또는 시스템 서비스를 변경하지 않습니다.

## 2. 가상 환경 및 테스트

\`\`\`bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m compileall -q enclaw_core
python -m unittest discover -s enclaw_core -t . -v
\`\`\`

테스트는 임시 Git 저장소를 만들어 실행합니다. 실제 프로젝트를 테스트 대상으로 지정하지 마세요.

## 3. 프로젝트 정책 만들기

\`\`\`bash
python -m enclaw_core.cli init-project /path/to/project --id my-project --base develop --integration enclaw/integration
python -m enclaw_core.cli validate /path/to/project/.enclaw/policy.json
python -m enclaw_core.cli show-policy /path/to/project/.enclaw/policy.json
\`\`\`

경로와 브랜치 이름을 실제 값으로 바꾸세요. 초기화 명령은 정책 파일만 생성하며 에이전트를 실행하거나 Git 브랜치를 자동으로 만들지 않습니다.

통합 브랜치를 직접 준비하려면 다음을 실행합니다.

\`\`\`bash
git -C /path/to/project branch enclaw/integration develop
git -C /path/to/project worktree add /path/to/project-integration enclaw/integration
\`\`\`

브랜치가 이미 있다면 첫 번째 명령을 반복하지 마세요. 작업 전 기본 체크아웃과 통합 worktree가 깨끗한지 확인합니다.

## 4. 작업 큐

\`\`\`bash
python -m enclaw_core.cli task-create --project my-project --title "테스트 작업" --description "작업 큐에 저장되는지 확인"
python -m enclaw_core.cli task-list --project my-project
\`\`\`

기본 SQLite DB는 사용자 홈 아래의 .local/state/enclaw/tasks.sqlite3입니다. ENCLAW_DB_PATH 환경 변수 또는 --db 옵션으로 위치를 변경할 수 있습니다. 작업 등록만으로 코딩 에이전트가 실행되지는 않습니다.

## 5. 프로젝트 레지스트리

\`\`\`bash
mkdir -p ~/.config/enclaw
cp config/projects.example.json ~/.config/enclaw/projects.json
\`\`\`

레지스트리 안의 프로젝트 ID와 정책 파일 경로를 실제 값으로 수정합니다. 정책 경로가 올바른 프로젝트의 .enclaw/policy.json을 가리키는지 확인하세요.

## 6. 코딩 워커 구성 — 주의

워커를 사용하려면 프로젝트 정책에서 runner.agent_command와 runner.review_command를 설정해야 합니다. 둘은 인자 배열이어야 하고, 서로 다른 명령이어야 하며, 각각 {prompt}를 포함해야 합니다. 플래너는 선택 사항이며 runner.planner_command를 별도로 설정해야 합니다.

모델 ID와 CLI 인자를 추측하지 말고 설치된 도구의 도움말로 확인하세요. 무료 API 여부는 공급자의 실제 가격과 사용 한도로 검증해야 합니다.

중요: EnClaw은 자체 OS/컨테이너 샌드박스를 제공하지 않습니다. 기본값 `runner.require_sandbox: true`에서는 `runner.sandbox_command`가 비어 있으면 에이전트 실행을 거부합니다. 이 설정은 지정된 명령 접두사를 붙이는 기능일 뿐, 접두사 자체가 안전한 격리를 보장하지는 않습니다. 신뢰할 수 있는 격리 도구로 설정하고 별도 테스트 저장소에서 검증하세요. `require_sandbox: false`는 신뢰할 수 있는 로컬 개발에서만 사용하세요.

## 7. CLI 작업 흐름

\`\`\`bash
python -m enclaw_core.cli task-list --project my-project
python -m enclaw_core.cli task-show TASK_ID
python -m enclaw_core.cli task-events TASK_ID
python -m enclaw_core.cli task-cancel TASK_ID
python -m enclaw_core.cli task-retry TASK_ID --policy /path/to/project/.enclaw/policy.json
\`\`\`

선택적 플래너 및 워커 풀:

\`\`\`bash
python -m enclaw_core.cli orchestrate-once --projects ~/.config/enclaw/projects.json --max-children 8
python -m enclaw_core.cli worker-pool --projects ~/.config/enclaw/projects.json --workers 2
python -m enclaw_core.cli orchestrate-refresh
\`\`\`

플래너는 기본 비활성화입니다. 워커 풀은 1~16개의 로컬 워커 스레드를 지원하지만 프로젝트별 동시 실행 한도는 정책에 의해 제한됩니다. 처음에는 작은 값으로 테스트하세요.

## 8. Discord 봇 설정 (선택)

Discord 개발자 포털에서 봇을 만들고 필요한 권한을 설정한 후 다음 환경 변수를 비공개 환경에 지정합니다.

- DISCORD_BOT_TOKEN
- DISCORD_GUILD_ID (서버별 명령 등록을 사용하는 경우)
- DISCORD_OPERATOR_IDS (쉼표로 구분된 숫자 사용자 ID)

토큰을 코드, README, 정책 파일 또는 Git에 넣지 마세요.

\`\`\`bash
python -m enclaw_core.discord_bot
\`\`\`

봇 명령 등록과 권한은 테스트 Discord 서버에서 확인하세요. 명령은 설정된 운영자 ID에 제한되어야 합니다.

## 현재 범위와 제한

비용 예산 강제, 역할별 에이전트 라우팅과 메시징, 신뢰할 수 있는 리뷰어 신원 증명, OS/컨테이너 샌드박스, 장애 후 자동 복구, 자동 PR 및 배포는 완성되지 않았습니다. 이 문서는 운영 서버 배포를 승인하는 문서가 아닙니다.

최신 구현 범위는 [구현 현황](IMPLEMENTATION_STATUS.md), CLI 세부 사항은 [코어 README](../enclaw_core/README.md)를 참고하세요.
