# BOSTO BT-101A под Linux

[English](README.md)

Драйвер-мост для USB-монитора **BOSTO BT-101A** на базе [Hermes-KMS](https://github.com/MrOz59/Hermes-KMS).

- **Система:** Arch Linux, GNOME Wayland
- **Устройство:** `33c3:0e02` (ArtInChip)
- **Экран:** 1280 × 800 @ 60 Гц
- **Кодирование:** JPEG, качество 90, 4:2:0
- **Передача:** GNOME → Hermes-KMS → DMA-BUF → JPEG → USB

Захват и отправка кадров работают в отдельных потоках. Буфер на один кадр заменяет устаревшие изображения свежими, не накапливая задержку.

## Установка

Нужны работающая сессия GNOME Wayland и драйвер видеокарты. Из каталога проекта:

```bash
./install.sh
```

Установщик поставит зависимости Arch (включая заголовки ядра и DKMS), при необходимости установит Hermes-KMS, соберёт мост, настроит доступ к USB и включит пользовательскую службу systemd.

После первой установки **один раз переподключи BOSTO**. Не запускай `hermes-kmsctl hold` одновременно с мостом.

## Настройки

При необходимости создай `~/.config/bosto-hermes.conf`:

```ini
BOSTO_FPS=60
BOSTO_QUALITY=90
```

Применить изменения:

```bash
systemctl --user restart bosto-hermes
```

## Управление и логи

```bash
systemctl --user status bosto-hermes
systemctl --user restart bosto-hermes
systemctl --user stop bosto-hermes
journalctl --user -u bosto-hermes -f
```

`USB ... FPS` — число отправленных кадров в секунду. `replaced` — число устаревших кадров, заменённых перед отправкой; это не ошибки USB.

Служба ожидает подключения устройства и повторяет попытки после отключения.

## Обновление, резервная копия, удаление

```bash
./update.sh      # Обновить установленный мост, сохранив предыдущую версию
./rollback.sh    # Вернуть предыдущую версию после обновления
./uninstall.sh   # Удалить мост и службу; Hermes-KMS останется
```

Установщик создаёт `~/bosto-hermes-reinstall.tar.gz` для переустановки системы. Сохрани архив. При наличии `hermes.lock` фиксирует коммит Hermes-KMS.

**Ограничение:** только вывод изображения; перо и сенсорный ввод не настраиваются.

## Благодарности

- [Hermes-KMS](https://github.com/MrOz59/Hermes-KMS) — виртуальный DRM-монитор
- [artinchip-linux](https://github.com/hevnsnt/artinchip-linux) — USB-протокол ArtInChip (MIT; лицензия включена)
