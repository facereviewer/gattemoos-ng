import re
from string import Formatter

from src.globals import *

class NumericEnum(Enum):
	def __init__(self, names):
		d = {name: i for i, name in enumerate(names)}
		super(NumericEnum, self).__init__(d)

class CustomFormatter(Formatter):
	def convert_field(self, value, conversion):
		if conversion == "x": # escape
			return escape_html(value)
		elif conversion == "t": # date[t]ime
			return format_datetime(value)
		elif conversion == "d": # time[d]elta
			return format_timedelta(value)
		return super(CustomFormatter, self).convert_field(value, conversion)

# definition of reply class and types

class Reply():
	def __init__(self, type, **kwargs):
		self.type = type
		self.kwargs = kwargs
		self.buttons = kwargs["buttons"] if "buttons" in kwargs else [[]]

types = NumericEnum([
	"CUSTOM",
	"SUCCESS",
	"SENSITIVE",
	"BOOLEAN_CONFIG",

	"CHAT_JOIN",
	"CHAT_LEAVE",
	"USER_IN_CHAT",
	"LOG_CHANNEL",
	"USER_NOT_IN_CHAT",
	"GIVEN_COOLDOWN",
	"MESSAGE_DELETED",
	"PROMOTED_MOD",
	"PROMOTED_ADMIN",
	"DEMOTED",
	"DEMOTELIST_INFO",
	"KARMA_THANK_YOU",
	"KARMA_NOTIFICATION",
	"TRIPCODE_INFO",
	"TRIPCODE_SET",
	"EXPOSE_TO",
	"EXPOSED",
	"NEW_USER",
	"WHITELIST_INFO",
	"UNBLACKLIST_INFO",

	"ERR_NO",
	"ERR_NO_EDITING",
	"ERR_COMMAND_DISABLED",
	"ERR_ADMIN_SEARCH",
	"ERR_NO_REPLY",
	"ERR_NOT_IN_CACHE",
	"ERR_NO_USER",
	"ERR_NO_USER_BY_ID",
	"ERR_COLLISION",
	"ERR_ALREADY_WARNED",
	"ERR_NOT_IN_COOLDOWN",
	"ERR_COOLDOWN",
	"ERR_NOTWHITELISTED",
	"ERR_ALREADY_WHITELISTED",
	"ERR_NOT_BLACKLISTED",
	"ERR_ALREADY_BLACKLISTED",
	"ERR_BLACKLISTED",
	"ERR_WITH_BLACKLISTED",
	"ERR_ALREADY_UPVOTED",
	"ERR_UPVOTE_OWN_MESSAGE",
	"ERR_SPAMMY",
	"ERR_SIGN_PRIVACY",
	"ERR_SPAMMY_TRIPCODE",
	"ERR_INVALID_TRIP_FORMAT",
	"ERR_NO_TRIPCODE",
	"ERR_NEED_TRIPCODE",
	"ERR_MEDIA_LIMIT",
	"ERR_EXPOSE_CONFIRM",
	"ERR_NO_WAITLIST",
	"ERR_NO_UNBLACKLIST",
	"ERR_NO_LIST",
	"ERR_NOTHING_TO_DO",

	"USER_INFO",
	"USER_INFO_MOD",
	"USERS_INFO",
	"USERS_INFO_EXTENDED",
	"POLL",

	"PROGRAM_START",
	"PROGRAM_VERSION",
	"HELP_MODERATOR",
	"HELP_ADMIN",
	"UNUSED_HELP_COMMAND"
])

# formatting of these as user-readable text

def em(s):
	# make commands clickable by excluding them from the formatting
	s = re.sub(r'[^a-z0-9_-]/[A-Za-z]+\b', r'</em>\g<0><em>', s)
	return "<em>" + s + "</em>"

def smiley(n):
	if n <= 0: return ":)"
	elif n == 1: return ":|"
	elif n <= 3: return ":/"
	else: return ":("

format_strs = {
	types.CUSTOM: "{text}",
	types.SUCCESS: "✅",
	types.SENSITIVE: "<em>(sensitive info deleted)</em>",
	types.BOOLEAN_CONFIG: lambda enabled, **_:
		"<b>{description!x}</b>: " + (enabled and "enabled" or "disabled"),

	types.CHAT_JOIN: em("You joined the chat!"),
	types.CHAT_LEAVE: em("You left the chat!"),
	types.LOG_CHANNEL: "gattemoos-ng started",
	types.USER_IN_CHAT: em("You're already in the chat."),
	types.USER_NOT_IN_CHAT: em("You're not in the chat yet. Use /start to join!"),
	types.GIVEN_COOLDOWN: lambda deleted, **_:
		em( "You've been handed a cooldown of {duration!d} for this message"+
			(deleted and " (message was deleted: {text!x})" or "") ),
	types.MESSAGE_DELETED:
		em( "Your message has been deleted, with no penalty." ),
	types.PROMOTED_MOD: em("You've been promoted to moderator, run /modhelp for a list of commands."),
	types.PROMOTED_ADMIN: em("You've been promoted to admin, run /adminhelp for a list of commands."),
	types.DEMOTED: "You were demoted.",
	types.DEMOTELIST_INFO: "Please select a mod or admin to demote:",
	types.KARMA_THANK_YOU: em("You just gave this user some sweet karma, awesome!"),
	types.KARMA_NOTIFICATION:
		em( "You got +1 karma!" ),
	types.TRIPCODE_INFO: lambda tripcode, **_:
		"<b>tripcode</b>:\n " + ("<code>{tripcode!x}</code>" if tripcode is not None else "unset"),
	types.TRIPCODE_SET: em("Tripcode set. It will appear as:\n") + "<b>{tripname!x}</b> <code>{triphash!x}</code>",
	types.EXPOSE_TO: lambda name, link, **_:
		"<i>{name} has revealed theirself to you privately as {link}!</i>",
	types.EXPOSED: em("Your real handle has been exposed to anon."),
	types.NEW_USER: "<b><i>A new user has started the bot.</i></b>",
	types.WHITELIST_INFO: em("Please select a recent user from the list below to add to the whitelist:"),
	types.UNBLACKLIST_INFO: em("Please select a banned user from the list below to remove from the blacklist:"),

	types.ERR_NO: em("Actually no"),
	types.ERR_NO_EDITING: em("Edits will not be seen by other members."),
	types.ERR_COMMAND_DISABLED: em("This command has been disabled."),
	types.ERR_ADMIN_SEARCH: em("Please only search by tripcode or OID."),
	types.ERR_NO_REPLY: em("You need to reply to a message to use this command."),
	types.ERR_NOT_IN_CACHE: em("Message not found in cache... (36h passed or bot was restarted)"),
	types.ERR_NO_USER: em("No user found by that name!"),
	types.ERR_NO_USER_BY_ID: em("No user found by that id! Note that all ids rotate every 24 hours."),
	types.ERR_COLLISION: em("More than one user currently has the same name. Try the command again while replying to their message or use a different kind of ID."),
	types.ERR_COOLDOWN: em("Your cooldown expires at {until!t}"),
	types.ERR_ALREADY_WARNED: em("A warning has already been issued for this message."),
	types.ERR_NOT_IN_COOLDOWN: em("This user is not in a cooldown right now."),
	types.ERR_NOTWHITELISTED: lambda contact, **_:
		em( "You haven't been whitelisted.") +
		( em("\ncontact:") + " {contact}" if contact else "" ),
	types.ERR_ALREADY_WHITELISTED: em("This user has already been added to the whitelist."),
	types.ERR_NOT_BLACKLISTED: em("This user has not been banned."),
	types.ERR_ALREADY_BLACKLISTED: em("This user has already been banned."),
	types.ERR_BLACKLISTED: lambda reason, contact, **_:
		em( "You've been blacklisted" + (reason and " for {reason!x}" or "") )+
		( em("\ncontact:") + " {contact}" if contact else "" ),
	types.ERR_WITH_BLACKLISTED: em("This user has been banned."),
	types.ERR_ALREADY_UPVOTED: em("You have already upvoted this message."),
	types.ERR_UPVOTE_OWN_MESSAGE: em("You can't upvote your own message."),
	types.ERR_SPAMMY: em("Your message has not been sent. Avoid sending messages too fast, try again later."),
	types.ERR_SIGN_PRIVACY: em("Your account privacy settings prevent usage of the sign feature. Enable linked forwards first."),
	types.ERR_SPAMMY_TRIPCODE: em("Your tripcode cannot be set for another {time_left} hours."),
	types.ERR_INVALID_TRIP_FORMAT:
		em("Given tripcode is not valid, the format is \n")+
		"<code>name#pass</code>" + em("\n where your chosen name is no more than 18 characters."),
	types.ERR_NO_TRIPCODE: em("You don't have a tripcode set."),
	types.ERR_NEED_TRIPCODE: "<i>This chat requires a tripcode to be set before you can send messages. Please use\n<code>/tripcode somename#apassword</code> where 'somename' is any name you'd like and 'apassword' is a secret password that will protect your identity.</i>",
	types.ERR_MEDIA_LIMIT: em("You can't send media or forward messages at this time, try again later."),
	types.ERR_EXPOSE_CONFIRM: "<i>This will expose your real username.\nPlease use <code>/exposeto yes</code> while replying to someone's message to confirm that you want to expose your username to them.</i>",
	types.ERR_NO_WAITLIST: em("There is no one waiting to be whitelisted."),
	types.ERR_NO_UNBLACKLIST: em("There has been no one blacklisted."),
	types.ERR_NO_LIST: em("There is no one to show."),
	types.ERR_NOTHING_TO_DO: em("Already done!"),

	types.USER_INFO: lambda warnings, cooldown, **_:
		"<b>id</b>: {id}, <b>name</b>: {username!x}\n<b>rank</b>: {rank_i} ({rank}), "+
		"<b>karma</b>: {karma}\n"+
		"<b>warnings</b>: {warnings} " + smiley(warnings)+
		( " (one warning will be removed on {warnExpiry!t})" if warnings > 0 else "" ) + ", "+
		"<b>cooldown</b>: "+
		( cooldown and "yes, until {cooldown!t}" or "no" ),
	types.USER_INFO_MOD: lambda cooldown, muzzled, **_:
		"<b>id</b>: {id}, <b>name</b>: {username!x}\n<b>rank</b>: {rank_i} ({rank}), "+
		"<b>karma</b>: {karma}\n"+
		"<b>cooldown</b>: "+
		( cooldown and "yes, until {cooldown!t}" or "no" ) + 
		( "\n<i>muzzled</i>" if muzzled else ""),
	types.USERS_INFO: "<b>{count}</b> <i>users</i>",
	types.USERS_INFO_EXTENDED:
		"<b>{active}</b> <i>active</i>, {inactive} <i>inactive and</i> "+
		"{blacklisted} <i>blacklisted users</i> (<i>total</i>: {total})",
	types.POLL: "Your poll has been forwarded anonymously.",

	types.PROGRAM_START: "<b>Secret Lounge has restarted.</b>\nsecretlounge-ng v{version}",
	types.PROGRAM_VERSION: "secretlounge-ng v{version}",
	types.HELP_MODERATOR:
		"<i>Moderators can use the following commands</i>:\n"+
		"  /modhelp - show this text\n"+
		"  /modsay &lt;message&gt; - send an official moderator message\n"+
		"\n"+
		"<i>Or reply to a message and use</i>:\n"+
		"  /info - get info about the user that sent this message\n"+
		"  /warn - warn the user that sent this message (cooldown)\n"+
		"  /lock - stops people from exposing themselves through that message (or /unlock)\n"+
		"  /blacklist [reason] - blacklist the user who sent this message (can also use /ban)\n"+
		"  /delete - delete a message and warn the user\n"+
		"  /remove - delete a message without a cooldown/warning\n"+
		"  /cleanup - remove all posts if the user was banned",
	types.HELP_ADMIN:
		"<i>Admins can use the following commands</i>:\n"+
		"  /adminhelp - show this text\n"+
		"  /adminsay &lt;message&gt; - send an official admin message\n"+
		"  /motd &lt;message&gt; - set the welcome message (HTML formatted)\n"+
		"  /uncooldown &lt;id | username&gt; - remove cooldown from a user\n"+
		"  /whitelist - Show a list of users to add to the whitelist.\n"+
		"  /whitelist &lt;username or id&gt; - Add a user to the whitelist. Usernames only work for users who have tried joining. \n"+
		"  /mod &lt;username&gt; - promote a user to the moderator rank\n"+
		"  /admin &lt;username&gt; - promote a user to the admin rank\n"+
		"  /demote - demote an admin or mod to user rank\n"+
		"  /muzzle - restricts karma and exposing (or /unmuzzle)\n" +
		"  /reset - resets a user's karma to 0\n" +
		"\n"+
		"<i>Or reply to a message and use</i>:\n"+
		"  /unblacklist - show a list of users to unban (can also use /unban)",
	types.UNUSED_HELP_COMMAND:#this could be hard-coded like so, instead of customized in the /help command.
	"<i>You can use the following commands</i>:\n"+	
	"  /stop — Stops the bot.\n"+
	"  /start — Restarts the bot.\n"+
	"  /users — Shows some stats.\n"+
	"  /info — See your own info, including your obfuscated ID (OID), your tripcode, your karma, and information about cooldowns and warnings.\n"+
	"  /motd — Shows the Message of the Day, which is just our /rules list.\n"+
	"  /togglekarma — Turn off the +1 Karma messages, or turn them back on.\n"+
	"  /tripcodetoggle — Keeps your tripcode turned on, so all your messages will be signed with your chosen pseudonym. <em>See below to set up the /tripcode.</em>\n"+
	"\n"+
	"<i>Or reply to someone else's message and use</i>:\n"+
	"  +1 — Give them some karma.\n"+
	"  /exposeto yes — Exposes your real identity to them. You have to type \"yes\" to avoid accidents.\n"+
	"\n"+
	"<i>These commands require you to type something in, similar to the examples shown.</i>:\n"+
	"  /tripcode <code>chosen name</code>#<code>password</code> — A tripcode is made from your chosen name, the # symbol, and a password. The password you enter will be transformed into some random characters that will let people verify it's truly you speaking. This allows you to build a name for yourself here. The name and hash will show when you use /t in front of your messages. <b>Note:</b> Everyone can copy each other's names, so make sure to verify that their random hash is the same if you want to be sure they're the same person. The hash is created from their password, so people can't easily get the same hash.\n"+
	"  /t or /tsign <code>my message</code> — Sends your message with your tripcode visible at the top, so everyone can verify that it was you who spoke.\n"+
	"\n"+
	"<i>Karma</i>:\n"+
	"  You get karma by receiving +1s from other people. \n"+
	"  You need 1 karma to post stickers, 2 karma to post images, and 5 karma to post videos.\n"+
	"  A warning from a moderator will lower your karma. You can end up with negative karma."

}

localization = {}

def formatForTelegram(m):
	s = localization.get(m.type)
	if s is None:
		s = format_strs[m.type]
	if type(s).__name__ == "function":
		s = s(**m.kwargs)
	cls = localization.get("_FORMATTER_", CustomFormatter)
	return cls().format(s, **m.kwargs)
