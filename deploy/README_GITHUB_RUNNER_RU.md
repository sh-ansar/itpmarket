# Автоматическое развёртывание с GitHub

Репозиторий: `https://github.com/sh-ansar/itpmarket.git`

## Сервер

Рекомендуемая структура:

```text
C:\ITPMarket\
├── app\       код приложения
└── shared\    резервные копии и постоянные серверные данные
```

На GitHub откройте:

```text
Settings → Actions → Runners → New self-hosted runner → Windows x64
```

Установите runner в `C:\actions-runner`, добавьте дополнительную метку
`itpmarket-demo` и установите его как Windows service.

Workflow `.github/workflows/deploy-windows.yml` запускается при push в `main`.
Он останавливает приложение, делает резервную копию базы, обновляет код,
проверяет зависимости, выполняет миграцию, запускает сервер и проверяет `/health`.

Не используйте self-hosted runner для недоверенных pull request из forks.
Репозиторий с серверным workflow рекомендуется держать закрытым.
