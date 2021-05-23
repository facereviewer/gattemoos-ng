secretlounge-ng
---------------
Rewrite of [secretlounge](https://github.com/6697/secretlounge), a bot to make an anonymous group chat on Telegram.
Further rewritten for furry purposes. This version is pseudonymous instead of anonymous.


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

### Trimmed Command List for ease of use
```
start - Join the chat (start receiving messages)
stop - Leave the chat (stop receiving messages)
users - Find out how many users are in the chat
info - Get info about your account
exposeto - Send your real username to someone else
motd - Show the welcome message
tripcode - Show or set a tripcode for your messages
```

## Running on a VPS
1. Get VPS hosting. Sign up. Purchase.
3. Open up a terminal or something? There are SSH programs you can use on Windows that might be graphical. You can use Windows Subsystem for Linux (WSL) to run ubuntu or such from a cmd window
2. `SSH root@xxx.xxx.xxx.xxx` (where the Xs are the server's IP address) using your server's root password
3. Make a user account to run the bots: `sudo adduser whomever` where 'whomever' is a name you choose. It'll ask you to make a password
4. Give the new account sudoing rights: `usermod -aG sudo whomever` (Again, 'whomever' is changed to the name of your new account)
5. `exit` out of there
6. You can use sftp to transfer files. On Windows, just get WinSCP and set up a connection to 'whomever' at the server's IP address, make sure you're in their ~ (home) directory in that right-hand panel, use the left-hand panel to browse to your secretlounge-ng-master folder, and just drag all the files across
7. `SSH whomever@xxx.xxx.xxx.xxx` to log in as the new user with the password you just set
8. You might need to `sudo apt-get update` and then `sudo apt-get install python3-pip
9. If you put everything into a secretlounge folder, `cd` into the folder
10. Install requirements: `sudo pip3 install -r requirements.txt` (You might also have to `sudo pip3 install pyTelegramBotAPI` separately for some reason?)
11. Copy default configuration: `cp config.yaml.example bot1/config.yaml`
12. `nano bot1/config.yaml` and paste in your bot key from BotFather (you might need to `sudo apt-get install nano`)
13. Turn the python file into a program: `sudo chmod 755`
14. You'll want to run it on the server and close the SSH window, so get `sudo apt-get install screen`

### Running:
1. `screen -dmS bot1` where 'bot1' can be any name you choose.
2. `./secretlounge-ng -c bot1/config.yaml`
3. On your keyboard, press `Ctrl + A`, then press `D` to leave that screen.
4. You can now `exit` and close your session

`screen` can be used to start multiple sessions. You can use `Ctrl + A, N` and `Ctrl + A, P` to go to the Next and Previous screens.

### Shutting it down:
1. `screen -r bot1` to resume
2. On your keyboard, press `Ctrl + C` to stop the program

## Create another bot
1. Make sure you're in the secretlounge-ng folder
1. `mkdir bot2`
2. `cp config.yaml.example bot2/config.yaml`
3. `nano bot2/config.yaml` and paste in your new bot key from BotFather. Also change 'bot1' to 'bot2' on the database line
4. `screen -dmS bot2`
5. When you start secretlounge-ng, use the -c flag: `./secretlounge-ng -c bot2/config.yaml`

## Security
You should harden your server by doing a few other things:
- Disable SSH for root
- Close most of the ports. Change defaults for SSH and SFTP

## FAQ

1. **How do I unban a blacklisted user from my bot?**

To unban someone you need their Telegram User ID (preferred) or username/profile name.
If you have a name you can use `./util/blacklist.py find` to search your bot's database for the user record.

You can then run `./util/blacklist.py unban 12345678` to remove the ban.

2. **How do I demote somone I promoted to mod/admin at some point?**

If you already have an User ID in mind, proceed below.
Otherwise you can either use the find utility like explained above or run
`./util/perms.py list` to list all users with elevated rank.

Simply run `./util/perms.py set 12345678 user` to remove the users' privileges.

This can also be used to grant an user higher privileges by exchanging the last argument with "*mod*" or "*admin*".

3. **What is the suggested setup to run multiple bots?**

The `blacklist.py` and `perms.py` script, including advanced functions like blacklist syncing
(`./util/blacklist.py sync`), support a structure like the following where each bot
has its' own subdirectory:

```
root folder
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
