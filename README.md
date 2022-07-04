# TGVMaxBot

A Telegram bot to automatically notify users when Max Jeune (formerly TGV Max)
tickets are available for specific dates and train stations.

## Installation

TGVMaxBot requires Python 3.7 or newer.

Dependencies can be installed using pip:

```bash
$ pip3 install -r requirements.txt
```

## Configuration

You need to create two files: `auth.ini` and `config.ini`:

```bash
$ cp auth.ini.example auth.ini
$ cp config.ini.example config.ini
```

### Telegram

To send messages using Telegram, you first need to create a [Telegram
bot](https://core.telegram.org/bots). For this, you need to talk to
[BotFather](https://t.me/botfather) and issue the `/newbot` command. Once you
are done, BotFather will provide you with a token, save it in the `auth.ini`
file.

Once this is done, you can invite your bot to a Telegram group or channel and
retrieve its chat id. After inviting the bot, you can find the chat ID from the
`https://api.telegram.org/bot{token}/getUpdates` URL (replacing `{token}` with
your bot's token).

### TGVMaxBot

Finally, you can configure this script by editing the `config.ini` file. Here
are the available options:

| Key                  | Type   | Description                                                               |
| -------------------- | ------ | ------------------------------------------------------------------------- |
| check_interval       | int    | How often to check for new posts (in seconds, default is 3600)            |
| allowed_chat_ids     | list   | A space separated list of chat IDs in which the bot will answer all users |
| allowed_user_ids     | list   | A space separated list of yser IDs to which the bot will always answer    |
| silent_notifications | bool   | Whether to send messages silently (notifications will have no sound)      |

## Running

Simply start the script and let it run in the background:

```bash
$ python3 main.py
```

### Docker

You can run the bot directly using [Docker](https://www.docker.com):

```bash
$ docker build . -t tgvmaxbot
$ docker run -d tgvmaxbot
```

### Systemd service

If you use systemd, you can run the bot as a service. Here is an example
configuration file:

```
[Unit]
Description=TGVMaxBot
After=network.target

[Service]
User=bot
Nice=1
KillMode=mixed
SuccessExitStatus=0 1
ProtectHome=true
ProtectSystem=full
PrivateDevices=true
NoNewPrivileges=true
WorkingDirectory=/var/bots/
ExecStart=/usr/bin/python3 /var/bots/TGVMaxBot/main.py
#ExecStop=

[Install]
WantedBy=multi-user.target
```

You can save it as `/etc/systemd/system/TGVMaxBot.service`, and then start it:

```bash
$ systemctl daemon-reload
$ systemctl enable TGVMaxBot
$ systemctl start TGVMaxBot
```
