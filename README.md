secretlounge-ng
---------------
Rewrite of [secretlounge](https://github.com/6697/secretlounge), a bot to make an anonymous group chat on Telegram.
Further rewritten for furry purposes. This version is pseudonymous instead of anonymous (though it still supports anonymous mode).


## @BotFather Setup
1. Message [@BotFather](https://t.me/BotFather)
2. Say "/newbot"
3. Give it a name and an ID
4. Remember to eventually set the userpic and the about text.
5. `/setprivacy`: enabled
6. `/setjoingroups`: disabled
7. `/setcommands`: paste the command list below

### Full Command list
```
start - Join the chat (start receiving messages)
stop - Leave the chat (stop receiving messages)
users - Find out how many users are in the chat
info - Get info about your account
exposeto - Send your real username to someone else
tsign - Sign a message with your tripcode
t - Alias of tsign
motd - Show the welcome message
version - Get version & source code of this bot
modhelp - Show commands available to moderators
adminhelp - Show commands available to admins
toggledebug - Toggle debug mode (sends back all messages to you)
togglekarma - Toggle karma notifications
tripcode - Show or set a tripcode for your messages
tripcodetoggle - Toggle tripcode to be on by default on messages
```
(By default, tripcodetoggle is turned off in config.)

(There are other commands, but they're part of /modhelp and /adminhelp)

### Trimmed Command List for ease of use
```
start - Join the chat (start receiving messages)
stop - Leave the chat (stop receiving messages)
users - Find out how many users are in the chat
exposeto - Send your real username to someone else
motd - Show the welcome message
tripcode - Show or set a tripcode for your messages
```

## Running on a server
1. You should either know this part, or get some general experience. Take a course! SSH, SFTP, and so on. Make sure you create a new user account to run the bots.
2. Setup: You might need to `sudo apt-get update` and then `sudo apt-get install python3-pip`. Use pip3 to install requirements.txt.
3. Copy default configuration from `config.yaml.example` to `bot1/config.yaml`. Edit `bot1/config.yaml` and paste in your bot key from BotFather.
4. Turn the python file into a program: `sudo chmod 755 secretlounge-ng`.
5. You'll want to run it on the server and close the SSH window, so use `sudo apt-get install screen`.

### Running:
1. `screen -dmS bot1` where 'bot1' can be any name you choose.
2. `./secretlounge-ng -c bot1/config.yaml`
3. On your keyboard, press `Ctrl + A`, then press `D` to leave that screen.
4. You can now `exit` and close your session

### Shutting it down:
1. `screen -r bot1` to resume
2. On your keyboard, press `Ctrl + C` to stop the program

## Create another bot
1. Make a `bot2` directory and copy `config.yaml.example` to `bot2/config.yaml`
2. Edit `bot2/config.yaml` and paste in your new bot key from BotFather. Also change 'bot1' to 'bot2' on the database line.
3. `screen -dmS bot2`
4. When you start secretlounge-ng, use the -c flag: `./secretlounge-ng -c bot2/config.yaml`

## Security
You should harden your server by doing a few other things:
- Disable SSH for root
- Close most of the ports. Change defaults for SSH and SFTP

## FAQ

1. **How do I ban/unban/whitelist/unwhitelist/etc a user?**

Generally speaking, you should reply to a user's message to perform an action on them. Reply to one of their messages with `/ban just because` or `/info` or `/warn`. Check out the `/modhelp` and `/adminhelp` commands.

Sometimes you can't access one of their messages. To whitelist a new user, unban someone who has no recent messages, or stuff like that, you can just use commands like `/whitelist` or `/unban` or `/demote` to show an anonymized list.

You can paste in their tripcode or obfuscated ID. For example, `/mod someone!d30I83hFJ2` will find that person and make them a moderator. Admins can also search by username or id, but be careful never to accidentally send that to everyone by forgetting the `/` at the start of your command. That sensitive data will be deleted automatically from your chat window.

(Banning by username doesn't currently work. If they've never said anything, you'll have to use the server script.)

2. **What is the suggested setup to run multiple bots?**

The administrative scripts support a structure like the following where each bot has its' own subdirectory:

```
secretlounge folder
\-- bot1
  \-- db.sqlite
  \-- config.yaml
\-- bot2
  \-- db.sqlite
  \-- config.yaml
\-- ...
\-- README.md
\-- secretlounge-ng
```
