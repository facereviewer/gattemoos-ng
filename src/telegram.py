from datetime import datetime
import telebot
from telebot import apihelper
import logging
import time
import json
import re

import traceback

import src.core as core
import src.replies as rp
from src.database import User
from src.util import MutablePriorityQueue
from src.globals import *

# Used with media_limit_period and media_enabled
MEDIA_FILTER_TYPES = ("photo", "animation", "video", "sticker", "document") #FIX: This is just used for media_limit_period and media_enabled, so we can add more media types here.
CAPTIONABLE_TYPES = ("photo", "audio", "animation", "document", "video", "voice")
HIDE_FORWARD_FROM = set([
	"anonymize_bot", "AnonFaceBot", "AnonymousForwarderBot", "anonomiserBot",
	"anonymous_forwarder_nashenasbot", "anonymous_forward_bot", "mirroring_bot",
	"anonymizbot", "ForwardsCoverBot", "anonymousmcjnbot", "MirroringBot",
	"anonymousforwarder_bot", "anonymousForwardBot", "anonymous_forwarder_bot",
	"anonymousforwardsbot", "HiddenlyBot", "ForwardCoveredBot", "anonym2bot",
	"AntiForwardedBot", "noforward_bot", "Anonymous_telegram_bot",
	"Forwards_Cover_Bot", "ForwardsHideBot", "ForwardsCoversBot",
	"NoForwardsSourceBot", "AntiForwarded_v2_Bot", "ForwardCoverzBot",
])
VENUE_PROPS = ("title", "address", "foursquare_id", "foursquare_type", "google_place_id", "google_place_type")

# Send-to types, who to reply to
EVENT = 1
PARENT = 2

# module variables
bot = None
db = None
ch = None
message_queue = None
registered_commands = {}

# settings
allow_documents = None
linked_network: dict = None
tripcode_toggle = None
allow_edits = None
media_allowed = None
media_karma = None
karma_needed = True
stored_key = None
mute = False

def init(config, _db, _ch):
	global bot, db, ch, message_queue, allow_documents, linked_network, tripcode_toggle, allow_edits, media_allowed, media_karma, karma_needed, VERSION, stored_key
	if config["bot_token"] == "":
		logging.error("No telegram token specified.")
		exit(1)

	stored_key = config["bot_token"]
	logging.getLogger("urllib3").setLevel(logging.WARNING) # very noisy with debug otherwise
	telebot.apihelper.READ_TIMEOUT = 20

	bot = telebot.TeleBot(config["bot_token"], threaded=False)

	db = _db # SQLiteDatabase
	ch = _ch # Cache
	message_queue = MutablePriorityQueue()

	allow_contacts = config.get("allow_contacts",False)
	allow_documents = config.get("allow_documents",False)
	linked_network = config.get("linked_network")
	tripcode_toggle = config.get("tripcode_toggle",False)
	media_allowed = config.get("media_allowed",False)
	media_karma = config.get("media_karma",["no",0,0])
	if str(media_karma[0]) == "no":
		karma_needed = False
	allow_edits = config.get("allow_edits", False)
	VERSION = config.get("vanity_version", "") or VERSION
	if linked_network is not None and not isinstance(linked_network, dict):
		logging.error("Wrong type for 'linked_network'")
		exit(1)

	types = ["text", "location", "venue"]
	if allow_contacts:
		types += ["contact"]
	if allow_documents:
		types += ["document"]
	types += ["animation", "photo", "video", "video_note"]
	types += ["audio", "sticker", "voice", "poll"]

	# Unused commands
	#cmds = [
	#	, "s", "sign", "me"
	#]
	
	# Trimmed command list
	cmds = [
		"start", "stop", "users", "info", "help", "rules", "motd", "toggledebug", "togglekarma", "version", "source", "modhelp", "adminhelp", "modsay", "adminsay", "mod", "admin", "demote", "warn", "delete", "remove", "uncooldown", "whitelist", "blacklist", "unblacklist", "exposeto", "tripcode", "tripcodetoggle", "ban", "unban", "unwhitelist", "t", "tsign", "lock", "unlock", "cleanup", "muzzle", "unmuzzle", "reset", "lockdown", "mute"
	]
	for c in cmds: # maps /<c> to the function cmd_<c>
		c = c.lower()
		registered_commands[c] = globals()["cmd_" + c]
	set_handler(relay, content_types=types)

	start_edit_listener()


	
	@bot.callback_query_handler(func=lambda call: True)
	def callback_query(call):
		c_user = db.getUser(id=call.from_user.id)
		msid = call.message.id

		# This will change if there are ever non-admin buttons
		if c_user.rank < RANKS.admin:
			return

		if call.data.find("_cancel") >= 0:
			core.delete_message(c_user, msid, False)
			try:
				bot.answer_callback_query(call.id, "Cancelled", show_alert=False)
			except Exception as e:
				return
		else:
			try:
				user = db.getUser(id=call.data[call.data.find("_")+1:])
				if call.data.startswith("whitelist_"):
					core.whitelist_user(c_user, user.id)
				if call.data.startswith("unblacklist_"):
					core.unblacklist_user(c_user, user.id)
				if call.data.startswith("demote_"):
					core.demote_user(c_user, user.id)
				core._push_system_message(rp.Reply(rp.types.SUCCESS), who=c_user)
				core.delete_message(c_user, msid, False)
			except KeyError as e:
				logging.error("User not found from "+call.data[:call.data.find("_")]+" button.")
				return #some kind of error message? no_user_found?
			try:
				bot.answer_callback_query(call.id, "", show_alert=False)
			except Exception as e:
				return

	core._push_system_message(rp.Reply(rp.types.PROGRAM_START, version=VERSION))

def set_handler(func, *args, **kwargs):
	def wrapper(*args, **kwargs):
		try:
			func(*args, **kwargs)
		except Exception as e:
			logging.exception("Exception raised in event handler")
	bot.message_handler(*args, **kwargs)(wrapper)

def run():
	while True:
		try:
			bot.polling(none_stop=True, long_polling_timeout=45)
		except Exception as e:
			# you're not supposed to call .polling() more than once but I'm left with no choice
			logging.warning("%s while polling Telegram, retrying.", type(e).__name__)
			#logging.error(traceback.print_exc())
			time.sleep(3)

def register_tasks(sched):
	# cache expiration
	def task():
		ids = ch.expire()
		if len(ids) == 0:
			return
		n = 0
		def f(item):
			nonlocal n
			if item.msid in ids:
				n += 1
				return True
			return False
		message_queue.delete(f)
		if n > 0:
			logging.warning("Failed to deliver %d messages before they expired from cache.", n)
	sched.register(task, hours=6) # (1/4) * cache duration

def start_edit_listener():
	@bot.edited_message_handler(func=lambda msg: True)
	def callback_query(ev):
		if ev.chat.id < 0:
			return
		c_user = db.getUser(id=ev.from_user.id)

		if not allow_edits:
			return core._push_system_message(rp.Reply(rp.types.ERR_NO_EDITING),who=c_user)

		if ev.content_type=="text":
			#Just need to use the right user_id, I think, and it'll come out with the correct message id.
			cache_msid = ch.lookupMapping(ev.from_user.id, data=ev.message_id)
			if cache_msid is None:
				# FIX: messages should be more like the rest, with ev
				core._push_system_message(rp.Reply(rp.types.ERR_NOT_IN_CACHE),who=c_user)
				return

			fmt = FormattedMessageBuilder(None, ev.caption, ev.text)
			formatter_replace_links(ev, fmt)
			formatter_network_links(fmt)
			# FIX: can store whether a tripcode was used
			# if tripcode or c_user.tripcodeToggle:
			if not tripcode_toggle or c_user.tripcodeToggle:
				if c_user.tripcode is None:
					core._push_system_message(rp.Reply(rp.types.ERR_NEED_TRIPCODE), who=c_user)

				formatter_tripcoded_message(c_user, fmt)
			formatter_edited_message(fmt)
			fmt = fmt.build()

			for user in db.iterateUsers():
				if user.id == ev.chat.id:
					continue
				chat_msid = ch.lookupMapping(user.id, msid=cache_msid)
				try:
					bot.edit_message_text(fmt.content, user.id, chat_msid, parse_mode="HTML")
				except Exception as e:
					logging.error("Error editing message. "+str(e))


# Wraps a telegram user in a consistent class (used by core.py)
class UserContainer():
	def __init__(self, u):
		self.id = u.id
		self.username = u.username
		self.realname = u.first_name
		if u.last_name is not None:
			self.realname += " " + u.last_name

def split_command(text):
	text = text.strip()
	if " " not in text:
		return text[1:].lower(), ""
	pos = text.find(" ")
	return text[1:pos].lower(), text[pos+1:].strip()

def takesArgument(optional=False, isUsername=False):
	def f(func):
		def wrap(ev):
			_, arg = split_command(ev.text)
			if arg == "" and not optional:
				return
			arg = removeSensitiveInfo(ev, arg)
			return func(ev, arg)
		return wrap
	return f

def removeSensitiveInfo(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = str(arg)
	# is username and not tripcode, or is ID, delete
	args = arg.split(" ")
	if (args[0].startswith("@") and args[0].find("!") < 0 or re.search("^[0-9+]{5,}$",args[0]) is not None):
		core.delete_message(c_user, ev.message_id, False)
		send_answer(ev, rp.Reply(rp.types.SENSITIVE))
		return "##"+arg
	return arg

def getUserIdFromReply(ev):
	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)
	cm = ch.getMessage(reply_msid)
	return cm.user_id
	
def wrap_core(func, reply_to=None):
	def f(ev):
		m = func(UserContainer(ev.from_user))
		send_answer(ev, m, reply_to=reply_to)
	return f

def send_answer(ev, m, reply_to=None):
	if m is None:
		return
	elif isinstance(m, list): #forwarding a bunch of messages
		for m2 in m:
			send_answer(ev, m2, reply_to)
		return

	if reply_to in (EVENT, PARENT):
		reply_to = ev.message_id
	if reply_to == PARENT and ev.reply_to_message is not None:
		reply_to = ev.reply_to_message.message_id
	def f(ev=ev, m=m):
		while True:
			try:
				send_to_single_inner(ev.chat.id, m, reply_to=reply_to)
			except telebot.apihelper.ApiException as e:
				logging.info("Send failed. ID: %d",ev.chat.id)
				retry = check_telegram_exc(e, None)
				if retry:
					continue
				return
			break

	try:
		user = db.getUser(id=ev.from_user.id)
	except KeyError as e:
		user = None # happens on e.g. /start
	put_into_queue(user, None, f)

# TODO: find a better place for this
def allow_message_text(text):
	if text is None or text == "":
		return True
	# Mathematical Alphanumeric Symbols: has convincing looking bold text
	if any(0x1D400 <= ord(c) <= 0x1D7FF for c in text):
		return False
	return True

# determine spam score for message `ev`
def calc_spam_score(ev):
	if not allow_message_text(ev.text) or not allow_message_text(ev.caption):
		return 999

	s = SCORE_BASE_MESSAGE
	if (ev.forward_from is not None or ev.forward_from_chat is not None
		or ev.json.get("forward_sender_name") is not None):
		s = SCORE_BASE_FORWARD

	if ev.content_type == "sticker":
		return SCORE_STICKER
	elif ev.content_type == "text":
		pass
	else:
		return s
	s += len(ev.text) * SCORE_TEXT_CHARACTER + ev.text.count("\n") * SCORE_TEXT_LINEBREAK
	return s

###

# Formatting for user messages, which are largely passed through as-is

class FormattedMessage():
	html: bool
	content: str
	def __init__(self, html, content):
		self.html = html
		self.content = content

class FormattedMessageBuilder():
	text_content: str
	# initialize builder with first argument that isn't None
	def __init__(self, *args):
		self.text_content = next(filter(lambda x: x is not None, args))
		self.inserts = {}
	def get_text(self):
		return self.text_content
	# insert `content` at `pos`, `html` indicates HTML or plaintext
	# if `pre` is set content will be inserted *before* existing insertions
	def insert(self, pos, content, html=False, pre=False):
		i = self.inserts.get(pos)
		if i is not None:
			cat = lambda a, b: (b + a) if pre else (a + b)
			# only turn insert into HTML if strictly necessary
			if i[0] == html:
				i = ( i[0], cat(i[1], content) )
			elif not i[0]:
				i = ( True, cat(escape_html(i[1]), content) )
			else: # not html
				i = ( True, cat(i[1], escape_html(content)) )
		else:
			i = (html, content)
		self.inserts[pos] = i
	def prepend(self, content, html=False):
		self.insert(0, content, html, True)
	def append(self, content, html=False):
		self.insert(len(self.text_content), content, html)
	def enclose(self, pos1, pos2, content_begin, content_end, html=False):
		self.insert(pos1, content_begin, html)
		self.insert(pos2, content_end, html, True)
	def build(self) -> FormattedMessage:
		if len(self.inserts) == 0:
			return
		html = any(i[0] for i in self.inserts.values())
		norm = lambda i: i[1] if i[0] == html else escape_html(i[1])
		s = ""
		for idx, c in enumerate(self.text_content):
			i = self.inserts.pop(idx, None)
			if i is not None:
				s += norm(i)
			s += escape_html(c) if html else c
		i = self.inserts.pop(len(self.text_content), None)
		if i is not None:
			s += norm(i)
		assert len(self.inserts) == 0
		return FormattedMessage(html, s)

# Append inline URLs from the message `ev` to `fmt` so they are preserved even
# if the original formatting is stripped
def formatter_replace_links(ev, fmt: FormattedMessageBuilder):
	entities = ev.caption_entities or ev.entities
	if entities is None:
		return
	for ent in entities:
		if ent.type == "text_link":
			if ent.url.startswith("tg://"):
				continue # doubt anyone needs these
			if "://t.me/" in ent.url and "?start=" in ent.url:
				continue # deep links look ugly and are likely not important
			fmt.append("\n(%s)" % ent.url)

# Add inline links for >>>/name/ syntax depending on configuration
def formatter_network_links(fmt: FormattedMessageBuilder):
	if not linked_network:
		return
	for m in re.finditer(r'>>>/([a-zA-Z0-9]+)/', fmt.get_text()):
		link = linked_network.get(m.group(1).lower())
		if link:
			# we use a tg:// URL here because it avoids web page preview
			fmt.enclose(m.start(), m.end(),
				"<a href=\"tg://resolve?domain=%s\">" % link, "</a>", True)

# Add exposed message formatting for User `user` to `fmt`
def formatter_expose_message(user: core.User, fmt: FormattedMessageBuilder):
	fmt.append(" <a href=\"tg://user?id=%d\">" % user.id, True)
	fmt.append("~~" + user.getFormattedName())
	fmt.append("</a>", True)

# Add tripcode message formatting for User `user` to `fmt`
def formatter_tripcoded_message(user: core.User, fmt: FormattedMessageBuilder):
	# due to how prepend() works the string is built right-to-left
	fmt.prepend("</code>:\n", True)
	fmt.prepend(user.triphash)
	fmt.prepend("</b><code>", True)
	fmt.prepend(user.tripname)
	fmt.prepend("<b>", True)

def formatter_edited_message(fmt: FormattedMessageBuilder):
	fmt.append("\n<code>            <em>(edited)</em></code>", True)

###

# Message sending (queue-related)

class QueueItem():
	__slots__ = ("user_id", "msid", "func")
	def __init__(self, user, msid, func):
		self.user_id = None # who this item is being delivered to
		if user is not None:
			self.user_id = user.id
		self.msid = msid # message id connected to this item
		self.func = func
	def call(self):
		try:
			self.func()
		except Exception as e:
			logging.exception("Exception raised during queued message")
			logging.info("Stuff about e: "+str(e))
			#FIX: Look for common errors like Handshake timeouts

def get_priority_for(user):
	#logging.info("\n"+str(type(user)))
	#logging.info(dir(user))
	if user is None:
		# user doesn't exist (yet): handle as rank=0, lastActive=<now>
		# cf. User.getMessagePriority in database.py
		return max(RANKS.values()) << 16
	return user.getMessagePriority()

def put_into_queue(user, msid, f):
	message_queue.put(get_priority_for(user), QueueItem(user, msid, f))

def send_thread():
	while True:
		item = message_queue.get()
		item.call()

###

# Message sending (functions)

def is_forward(ev):
	return (ev.forward_from is not None or ev.forward_from_chat is not None
		or ev.json.get("forward_sender_name") is not None)
def get_forwardid(ev): #FIX: Probably needs a ev.json.get("forward_sender_id") or something
	return (ev.forward_from.id if ev.forward_from else ev.forward_from_chat.id if ev.forward_from_chat else None)

def should_hide_forward(ev):
	# Hide forwards from anonymizing bots that have recently become popular.
	# The main reason is that the bot API heavily penalizes forwarding and the
	# 'Forwarded from Anonymize Bot' provides no additional/useful information.
	if ev.forward_from is not None:
		return ev.forward_from.username in HIDE_FORWARD_FROM
	return False

def resend_message(chat_id, ev, reply_to=None, force_caption: FormattedMessage=None):

	# logging.info("from: "+str(ev.forward_from))
	if should_hide_forward(ev):
		pass
	elif is_forward(ev) and get_forwardid(ev) == ev.from_user.id:
		pass
	elif is_forward(ev):
		# forward message instead of re-sending the contents
		return bot.forward_message(chat_id, ev.chat.id, ev.message_id)

	kwargs = {}
	if reply_to is not None:
		kwargs["reply_to_message_id"] = reply_to
		kwargs["allow_sending_without_reply"] = True
	if ev.content_type in CAPTIONABLE_TYPES:
		if force_caption is not None:
			kwargs["caption"] = force_caption.content
			if force_caption.html:
				kwargs["parse_mode"] = "HTML"
		else:
			kwargs["caption"] = ev.caption

	# re-send message based on content type
	if ev.content_type == "text":
		return bot.send_message(chat_id, ev.text, **kwargs)
	elif ev.content_type == "photo":
		photo = sorted(ev.photo, key=lambda e: e.width*e.height, reverse=True)[0]
		return bot.send_photo(chat_id, photo.file_id, **kwargs)
	elif ev.content_type == "audio":
		for prop in ("performer", "title"):
			kwargs[prop] = getattr(ev.audio, prop)
		return bot.send_audio(chat_id, ev.audio.file_id, **kwargs)
	elif ev.content_type == "animation":
		return bot.send_animation(chat_id, ev.animation.file_id, **kwargs)
	elif ev.content_type == "document":
		return bot.send_document(chat_id, ev.document.file_id, **kwargs)
	elif ev.content_type == "video":
		return bot.send_video(chat_id, ev.video.file_id, **kwargs)
	elif ev.content_type == "voice":
		return bot.send_voice(chat_id, ev.voice.file_id, **kwargs)
	elif ev.content_type == "video_note":
		return bot.send_video_note(chat_id, ev.video_note.file_id, **kwargs)
	elif ev.content_type == "location":
		kwargs["latitude"] = ev.location.latitude
		kwargs["longitude"] = ev.location.longitude
		return bot.send_location(chat_id, **kwargs)
	elif ev.content_type == "venue":
		kwargs["latitude"] = ev.venue.location.latitude
		kwargs["longitude"] = ev.venue.location.longitude
		for prop in VENUE_PROPS:
			kwargs[prop] = getattr(ev.venue, prop)
		return bot.send_venue(chat_id, **kwargs)
	elif ev.content_type == "contact":
		for prop in ("phone_number", "first_name", "last_name"):
			kwargs[prop] = getattr(ev.contact, prop)
		return bot.send_contact(chat_id, **kwargs)
	elif ev.content_type == "sticker":
		return bot.send_sticker(chat_id, ev.sticker.file_id, **kwargs)
	elif ev.content_type == "poll":
		return bot.forward_message(chat_id, ev.chat.id, ev.message_id)

	else:
		raise NotImplementedError("content_type = %s" % ev.content_type)

# send a message `ev` (multiple types possible) to Telegram ID `chat_id`
# returns the sent Telegram message
def send_to_single_inner(chat_id, ev, reply_to=None, force_caption=None):
	
	if isinstance(ev, rp.Reply): # System message?
		kwargs2 = {}
		if reply_to is not None:
			kwargs2["reply_to_message_id"] = reply_to
			kwargs2["allow_sending_without_reply"] = True
		if ev.type == rp.types.CUSTOM:
			kwargs2["disable_web_page_preview"] = True
		if not ev.buttons == [[]]:
			#FIX: I'm supposed to be like...
			# markup = types.ReplyKeyboardMarkup(row_width=1)
			# itembtn1 = types.KeyboardButton('a')
			# itembtn2 = types.KeyboardButton('v')
			# markup.add(ev.buttons)
			# kwargs2["reply_markup"] = markup
			kwargs2["reply_markup"] = json.dumps({"inline_keyboard": ev.buttons})
		return bot.send_message(chat_id, rp.formatForTelegram(ev), parse_mode="HTML", **kwargs2)
	elif isinstance(ev, FormattedMessage): # Tripcode
		kwargs2 = {}
		if reply_to is not None:
			kwargs2["reply_to_message_id"] = reply_to
			kwargs2["allow_sending_without_reply"] = True
		if ev.html:
			kwargs2["parse_mode"] = "HTML"

		return bot.send_message(chat_id, ev.content, **kwargs2)

	# Non-tripcode, but maybe media.

	return resend_message(chat_id, ev, reply_to=reply_to, force_caption=force_caption)

# queue sending of a single message `ev` (multiple types possible) to User `user`
# this includes saving of the sent message id to the cache mapping.
# `reply_msid` can be a msid of the message that will be replied to
# `force_caption` can be a FormattedMessage to set the caption for resent media
def send_to_single(ev, msid, user, *, reply_msid=None, force_caption=None):
	user_id = user.id
	if reply_msid is not None:
		reply_to = ch.lookupMapping(user.id, msid=reply_msid)
		if reply_to is None:
			core._push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>This message was sent before you\narrived, or no longer exists.</i>"), who=user, msid=reply_msid)
			logging.info(f"reply associated with {reply_msid}")
	def f():
		while True:
			count = 1
			try:
				# set reply_to_message_id if applicable
				reply_to = None
				if reply_msid is not None:
					reply_to = ch.lookupMapping(user.id, msid=reply_msid)
					if reply_to is None:
						logging.info("Likely replying to a deleted message.")
					elif reply_to == -1:
						logging.info(f"User {user.id} still had {msid} as -1 at ToF.")
				ev2 = send_to_single_inner(user_id, ev, reply_to, force_caption)
			#FIX: This is because of my deletion code
			except telebot.apihelper.ApiTelegramException as e:
				logging.info(f"Error sending single: {e}")
				if str(e).find("user is deactivated") >= 0:
					core.force_user_leave(user.id)
				if str(e).find("bot was blocked") >= 0:
					core.force_user_leave(user.id)
				return
			except telebot.apihelper.ApiException as e:
				retry = check_telegram_exc(e, user_id)
				if retry and count < 5:
					count += 1
					logging.info(f"Bad thing, retrying... {e}")
					time.sleep(1)
					continue
				return
			break
		ch.saveMapping(user_id, msid, ev2.message_id)

		time.sleep(0.1)
		# pauses after sending, might help with rate limits.


	put_into_queue(user, msid, f)


# look at given Exception `e`, force-leave user if bot was blocked
# returns True if message sending should be retried
def check_telegram_exc(e, user_id):
	errmsgs = ["bot was blocked by the user", "user is deactivated",
		"PEER_ID_INVALID", "bot can't initiate conversation"]
	if any(msg in e.result.text for msg in errmsgs):
		if user_id is not None:
			core.force_user_leave(user_id)
		return False

	if "Too Many Requests" in e.result.text:
		d = json.loads(e.result.text)["parameters"]["retry_after"]
		d = min(d, 30) # supposedly this is in seconds, but you sometimes get 100 or even 2000
		logging.warning("API rate limit hit, waiting for %ds", d)
		time.sleep(d)
		return True # retry

	logging.exception("API exception")
	return False

####

# Event receiver: handles all things the core decides to do "on its own":
# e.g. karma notifications, deletion of messages, exposed messages
# This does *not* include direct replies to commands or relaying of messages.

@core.registerReceiver
class MyReceiver(core.Receiver):
	@staticmethod
	def reply(m, msid, who, except_who, reply_msid):
		if who is not None:
			return send_to_single(m, msid, who, reply_msid=reply_msid)

		for user in db.iterateUsers():
			if not user.isJoined():
				continue
			if user == except_who and not user.debugEnabled:
				continue
			# Stub mapping so that instant sends can be told that there is an EXPECTED msid involved, even if there's none now.
			# Fix: some places should probably check if msid is -1 and throw a "not found" anyway.
			ch.saveMapping(user.id, msid, -1)
			send_to_single(m, msid, user, reply_msid=reply_msid)

	@staticmethod
	def delete(msid, user_id=None):
		tmp = ch.getMessage(msid)
		# The following stops it from being deleted on the original user's end.
		#except_id = None if tmp is None else tmp.user_id 

		#FIX: If it's something like modsay, delete. If user message, don't.
		message_queue.delete(lambda item, msid=msid: item.msid == msid)
		# FIXME: there's a hard to avoid race condition here:
		# if a message is currently being sent, but finishes after we grab the
		# message ids it will never be deleted

		# When the system deletes the user's message without their input
		# (Just used for sensitive data right now, maybe polls)
		# FIX: This is different from stock SecretLounge, and doesn't work unless the user ID is passed in, even though it's not needed. Uh, just don't require it? Or pull it from the msid?
		try:
			id = ch.lookupMapping(user_id, msid=msid)
			bot.delete_message(user_id, msid)
		except telebot.apihelper.ApiException as e:
			logging.info("Stock Delete failed. ID: %d",user_id)

		for user in db.iterateUsers():
			# if not user.isJoined():
			# 	continue
			# if user.id == except_id:
			# 	continue

			id = ch.lookupMapping(user.id, msid=msid)
			if id is None:
				continue
			user_id = user.id
			def f(user_id=user_id, id=id):
				count = 0
				while True:
					count += 1
					try:
						bot.delete_message(user_id, id)
					except telebot.apihelper.ApiTelegramException as e:
						logging.info("API Error. Already deleted.")
						return
					except telebot.apihelper.ApiException as e:
						logging.info("Delete failed 2. ID: %d",user_id)
						retry = check_telegram_exc(e, None)
						if count >= 10:
							logging.info(f"Delete failed because of long wait. {user_id}:{id}")
							return
						if retry and count < 10:
							continue
						return
					break
			# queued message has msid=None here since this is a deletion, not a message being sent
			put_into_queue(user, None, f)
		# drop the mappings for this message so the id doesn't end up used e.g. for replies
		ch.deleteMappings(msid)
	@staticmethod
	def stop_invoked(user, delete_out):
		message_queue.delete(lambda item, user_id=user.id: item.user_id == user_id)
		if not delete_out:
			return
		# delete all (pending) outgoing messages written by the user
		def f(item):
			if item.msid is None:
				return False
			cm = ch.getMessage(item.msid)
			if cm is None:
				return False
			return cm.user_id == user.id
		message_queue.delete(f)


#dict list parse except
#json.dumps(msg, default=lambda o: o.__dict__, sort_keys=True, indent=4)







####

#cmd_start = wrap_core(core.user_join)
def cmd_start(ev):
	message = core.user_join(UserContainer(ev.from_user))
	return send_answer(ev, message)


cmd_stop = wrap_core(core.user_leave)


cmd_users = wrap_core(core.get_users)


@takesArgument(optional=True, isUsername=True)
def cmd_info(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.get_info_mod(c_user, arg))

	if ev.reply_to_message is None:
		return send_answer(ev, core.get_info(c_user), reply_to=EVENT)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)
	cm = ch.getMessage(reply_msid)

	return send_answer(ev, core.get_info_mod(c_user, cm.user_id), reply_to=PARENT)


# FIX: Could add a special method that looks for extra_{something} and create as many custom commands as needed.
@takesArgument(optional=True)
def cmd_help(ev, arg):
	c_user = UserContainer(ev.from_user)

	if arg == "":
		send_answer(ev, core.get_help(c_user), reply_to=EVENT)
	else:
		send_answer(ev, core.set_help(c_user, arg), reply_to=EVENT)

@takesArgument(optional=True)
def cmd_motd(ev, arg):
	c_user = UserContainer(ev.from_user)

	if arg == "":
		send_answer(ev, core.get_motd(c_user), reply_to=EVENT)
	else:
		send_answer(ev, core.set_motd(c_user, arg), reply_to=EVENT)

cmd_toggledebug = wrap_core(core.toggle_debug)
cmd_togglekarma = wrap_core(core.toggle_karma)
cmd_tripcodetoggle = wrap_core(core.toggle_tripcode)

@takesArgument(optional=True)
def cmd_tripcode(ev, arg):
	c_user = UserContainer(ev.from_user)

	if arg.strip():
		send_answer(ev, core.set_tripcode(c_user, arg))
	else:
		send_answer(ev, core.get_tripcode(c_user))


cmd_modhelp = wrap_core(core.modhelp, reply_to=EVENT)
cmd_adminhelp = wrap_core(core.adminhelp, reply_to=EVENT)

def cmd_version(ev):
	send_answer(ev, rp.Reply(rp.types.PROGRAM_VERSION, version=VERSION), reply_to=EVENT)

cmd_source = cmd_version # alias


@takesArgument()
def cmd_modsay(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = escape_html(arg)
	return send_answer(ev, core.send_mod_message(c_user, arg), reply_to=EVENT)

@takesArgument()
def cmd_adminsay(ev, arg):
	c_user = UserContainer(ev.from_user)
	arg = escape_html(arg)
	return send_answer(ev, core.send_admin_message(c_user, arg), reply_to=EVENT)

@takesArgument(optional=True, isUsername=True)
def cmd_mod(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.promote_user(c_user, arg, RANKS.mod))

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)
	user_id = getUserIdFromReply(ev)

	return send_answer(ev, core.promote_user(c_user, user_id, RANKS.mod), reply_to=EVENT)

@takesArgument(optional=True, isUsername=True)
def cmd_admin(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.promote_user(c_user, arg, RANKS.admin))

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)
	user_id = getUserIdFromReply(ev)

	return send_answer(ev, core.promote_user(c_user, user_id, RANKS.admin), reply_to=EVENT)

def cmd_warn(ev, delete=False, only_delete=False):
	c_user = UserContainer(ev.from_user)

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	messagetext = ev.reply_to_message.text

	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)
	if only_delete:
		r = core.delete_message(c_user, reply_msid, text=messagetext)
	else:
		r = core.warn_user(c_user, reply_msid, delete, text=messagetext)
	send_answer(ev, r, reply_to=PARENT)

cmd_delete = lambda ev: cmd_warn(ev, delete=True)

cmd_remove = lambda ev: cmd_warn(ev, only_delete=True)

@takesArgument(optional=True, isUsername=True)
def cmd_uncooldown(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.uncooldown_user(c_user, arg))

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)
	user_id = getUserIdFromReply(ev)

	return send_answer(ev, core.uncooldown_user(c_user, user_id), reply_to=EVENT)

@takesArgument(optional=True, isUsername=True)
def cmd_whitelist(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.whitelist_user(c_user, arg))

	if ev.reply_to_message is None:
		return send_answer(ev, core.show_whitelist(c_user))
	user_id = getUserIdFromReply(ev)

	return send_answer(ev, core.whitelist_user(c_user, user_id), reply_to=EVENT)

@takesArgument(optional=True, isUsername=True)
def cmd_unwhitelist(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.unwhitelist_user(c_user, arg))

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)
	user_id = getUserIdFromReply(ev)

	return send_answer(ev, core.unwhitelist_user(c_user, user_id), reply_to=EVENT)

#FIX: This is probably more secure
# @bot.callback_query_handler(func=lambda call: True)
# def  test_callback(call):
# 	core.whitelist_reply(call)

@takesArgument(optional=True)
def cmd_blacklist(ev, arg):
	c_user = UserContainer(ev.from_user)

	#This whole thing isn't perfect. It's slightly possible that a mod could accidentally not reply, and then /ban with a four-letter word at the start of the reason, and it just HAPPENS that the word is someone's random code for today, and that person would be banned. Whups!
	if ev.reply_to_message is None:
		username = arg
		if " " not in username:
			arg = ""
		else:
			pos = username.find(" ")
			arg = username[pos+1:].strip()
			username = username[:pos].lower()
		if username:
			username = removeSensitiveInfo(ev, username)
			return send_answer(ev, core.blacklist_user(c_user, username, arg))
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)

	messagetext = ev.reply_to_message.text
	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)
	cm = ch.getMessage(reply_msid)

	return send_answer(ev, core.blacklist_user(c_user, cm.user_id, arg, msid=reply_msid, text=messagetext), reply_to=PARENT)

@takesArgument(optional=True, isUsername=True)
def cmd_unblacklist(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.unblacklist_user(c_user, arg))
	if ev.reply_to_message is None:
		return send_answer(ev, core.show_unblacklist(c_user))

	user_id = getUserIdFromReply(ev)

	return send_answer(ev, core.unblacklist_user(c_user, user_id), reply_to=EVENT)

@takesArgument(optional=True, isUsername=True)
def cmd_demote(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.demote_user(c_user, arg))
	if ev.reply_to_message is None:
		return send_answer(ev, core.show_demotelist(c_user))

	user_id = getUserIdFromReply(ev)

	return send_answer(ev, core.demote_user(c_user, user_id), reply_to=EVENT)

def plusone(ev):
	c_user = UserContainer(ev.from_user)
	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)
	return send_answer(ev, core.give_karma(c_user, reply_msid), reply_to=PARENT)

def adminreport(ev):
	c_user = UserContainer(ev.from_user)
	# if ev.reply_to_message is None:
	# 	return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)

	if ev.reply_to_message:
		reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
		if reply_msid is None:
			return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)
	else:
		reply_msid = None
	m = core.call_admin(c_user, reply_msid)
	if m.type == rp.types.CUSTOM:
		bot.send_message(-1001715277064, "<b>ALERT!</b> 🚨\n@admin was called for in the chat.\n\n@DogMikeZC\n@Sneplepblep\n@talkinguser", parse_mode="HTML")
	return send_answer(ev, m, reply_to=PARENT)

@takesArgument(optional=True, isUsername=True)
def cmd_reset(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.reset_karma(c_user, arg))

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)
	cm = ch.getMessage(reply_msid)

	return send_answer(ev, core.reset_karma(c_user, cm.user_id), reply_to=PARENT)



# This is the entry point for handling messages from Telegram.
# It just splits off some commands or tripcoded media, then goes to inner.
def relay(ev):
	# handle commands and karma giving
	if ev.chat.id < 0 and not (ev.text is not None and ev.text.startswith("/sus")):
		return
	if ev.content_type == "text":
		if ev.text.startswith("/"):
			c, _ = split_command(ev.text)
			if c in registered_commands.keys():
				registered_commands[c](ev)
			return
		elif ev.text.strip() == "+1":
			return plusone(ev)
		elif ev.text.strip().startswith("@admin"):
			return adminreport(ev)
	# manually handle signing / tripcodes for media since captions don't count for commands
	if not is_forward(ev) and ev.content_type in CAPTIONABLE_TYPES and (ev.caption or "").startswith("/"):
		c, arg = split_command(ev.caption)
		# This code used to display a message as the user
		# if c in ("sign"):
		# 	return relay_inner(ev, caption_text=arg, sign=True)
		if c in ("t", "tsign"):
			return relay_inner(ev, caption_text=arg, tripcode=True)

	relay_inner(ev)

# relay the message `ev` to other users in the chat
# `caption_text` can be a FormattedMessage that overrides the caption of media
# `expose` and `tripcode` indicate if the message is exposed or tripcoded
# returns void
def relay_inner(ev, *, caption_text=None, expose=False, signed=False, tripcode=False):
	is_media = is_forward(ev) or ev.content_type in MEDIA_FILTER_TYPES
	msid = core.prepare_user_message(UserContainer(ev.from_user), calc_spam_score(ev), is_media=is_media, expose=expose, tripcode=tripcode)
	if msid is None or isinstance(msid, rp.Reply):
		return send_answer(ev, msid) # don't relay message, instead reply

	user = db.getUser(id=ev.from_user.id)

	if hasattr(ev,"text") and ev.text is not None and ev.text.find("karma") >= 0:
		cm = ch.getMessage(msid)
		if cm:
			cm.locked = True
		else:
			logging.info("Just making sure cm always exists.") # FIX: delete later.

	# For signed msgs: check user's forward privacy status first
	# FIXME? this is a possible bottleneck
	if signed:
		tchat = bot.get_chat(user.id)
		if tchat.has_private_forwards:
			return send_answer(ev, rp.Reply(rp.types.ERR_SIGN_PRIVACY))

	# apply text formatting to text or caption (if media)
	ev_tosend = ev
	force_caption = None
	if is_forward(ev):
		pass # leave message alone
	elif ev.content_type == "text" or ev.caption is not None or caption_text is not None:
		fmt = FormattedMessageBuilder(caption_text, ev.caption, ev.text)
		formatter_replace_links(ev, fmt)
		formatter_network_links(fmt)
		if tripcode or not tripcode_toggle or user.tripcodeToggle:
			if user.tripcode is None:
				return send_answer(ev, rp.Reply(rp.types.ERR_NEED_TRIPCODE))
			formatter_tripcoded_message(user, fmt)
		fmt = fmt.build()
		if fmt is not None:
			fmt.from_user = user
		# either replace whole message or just the caption
		if ev.content_type == "text":
			ev_tosend = fmt or ev_tosend
		else:
			force_caption = fmt

	#FIX: All these _push_system_messages should be send_answer
	if is_media and not media_allowed:
		core._push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>Media posting has been disabled.</i>"), who=user)
		return

	# FIX: This is ugly
	if user.rank < RANKS.admin and karma_needed:
		if ev.content_type in ["sticker"]:
			if media_karma[MEDIA.stickers] < 0:
				core._push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>Sticker posting has been disabled.</i>"), who=user)
				return
			if user.karma < media_karma[MEDIA.stickers]:
				core._push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>You need %d more karma before you can send stickers.</i>"%(media_karma[MEDIA.stickers]-user.karma)), who=user)
				return
		if ev.content_type in ["photo"]:
			if media_karma[MEDIA.photos] < 0:
				core._push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>Photo posting has been disabled.</i>"), who=user)
				return
			if user.karma < media_karma[MEDIA.photos]:
				core._push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>You need %d more karma before you can send images.</i>"%(media_karma[MEDIA.photos]-user.karma)), who=user)
				return
		if ev.content_type in ["animation", "video"]:
			if media_karma[MEDIA.videos] < 0:
				core._push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>Video posting has been disabled.</i>"), who=user)
				return
			if user.karma < media_karma[MEDIA.videos]:
				core._push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>You need %d more karma before you can send GIFs or videos.</i>"%(media_karma[MEDIA.videos]-user.karma)), who=user)
				return


	if ev.content_type == "poll" and not is_forward(ev):
		core._push_system_message(rp.Reply(rp.types.POLL), who=user)
		kwargs2 = {}
		kwargs2["is_anonymous"]=True #Careful!
		kwargs2["type"]=ev.poll.type
		kwargs2["allows_multiple_answers"]=ev.poll.allows_multiple_answers
		kwargs2["correct_option_id"]=ev.poll.correct_option_id
		kwargs2["explanation"]=ev.poll.explanation
		kwargs2["open_period"]=ev.poll.open_period
		kwargs2["close_date"]=ev.poll.close_date
		ch.saveMapping(user.id, msid, ev.message_id)
		core.delete_message(user, msid, False)

		poll = bot.send_poll(user.id, question=ev.poll.question, options=ev.poll.options, **kwargs2)
		msid = core.prepare_user_message(user, calc_spam_score(ev))
		ev = poll
		ev_tosend = ev
		ev_tosend.from_user = user

	# find out which message is being replied to
	reply_msid = None
	if ev.reply_to_message is not None:
		reply_msid = ch.lookupMapping(user.id, data=ev.reply_to_message.message_id)
		if reply_msid is None:
			logging.warning("Message replied to not found in cache")
			reply_msid = core._push_system_message(rp.Reply(rp.types.CUSTOM,text="<i>[an uncached text]</i>"), except_who=user)
			ch.saveMapping(user.id, reply_msid, ev.reply_to_message.message_id)
			logging.info(f"[uncached text] is now cmid {reply_msid}")
			# Now it is in the cache.
			
			# FIX: All system messages should have a standard ID, and when that ID is encountered I should not generate this. Only when the message is completely missing (i.e. the bot has restarted) should I generate these.


	# relay message to all other users
	logging.debug("relay(): msid=%d reply_msid=%r", msid, reply_msid)
	ch.saveMapping(user.id, msid, ev.message_id)

	for user2 in db.iterateUsers():
		if not user2.isJoined():
			continue
		if user2 == user and not user.debugEnabled:
			continue

		if mute and user.rank < RANKS.admin and user2.rank < RANKS.admin:
			# return send_answer(ev, rp.Reply(rp.types.CUSTOM, text="<i>The chat has been muted. Others cannot see your post.</i>"))
			continue
		if mute and user.rank < RANKS.admin: # test if only admins see this person's messages.
			logging.info(f"{user2} saw message from {user}!")

		# Stub mapping so that instant sends can be told that there is an EXPECTED msid involved, even if there's none now.
		# Fix: some places should probably check if msid is -1 and throw a "not found" anyway.
		ch.saveMapping(user2.id, msid, -1)
		send_to_single(ev_tosend, msid, user2,
			reply_msid=reply_msid, force_caption=force_caption)


@takesArgument(optional=True, isUsername=True)
def cmd_exposeto(ev, arg):
	user = db.getUser(id=ev.from_user.id)
	if not arg:
		return send_answer(ev, rp.Reply(rp.types.ERR_EXPOSE_CONFIRM), reply_to=EVENT)

	if ev.reply_to_message is None:
		return send_answer(ev, core.expose_to_user(user,None,arg))	

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)

	return send_answer(ev, core.expose_to_user(user,reply_msid,arg), reply_to=PARENT)	

#cmd_s = cmd_sign # alias

@takesArgument()
def cmd_tsign(ev, arg):
	ev.text = arg
	relay_inner(ev, tripcode=True)

def cmd_lock(ev):
	c_user = UserContainer(ev.from_user)

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)

	messagetext = ev.reply_to_message.text
	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)

	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)

	send_answer(ev, core.lock_message(c_user, reply_msid, text=messagetext), reply_to=PARENT)

def cmd_unlock(ev):
	c_user = UserContainer(ev.from_user)

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)

	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)

	send_answer(ev, core.unlock_message(c_user, reply_msid), reply_to=PARENT)


@takesArgument(optional=True, isUsername=True)
def cmd_muzzle(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.muzzle_user(c_user, arg))

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)
	cm = ch.getMessage(reply_msid)

	return send_answer(ev, core.muzzle_user(c_user, cm.user_id), reply_to=PARENT)

@takesArgument(optional=True, isUsername=True)
def cmd_unmuzzle(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.muzzle_user(c_user, arg, toMuzzle=False))

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)

	reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)
	if reply_msid is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)
	cm = ch.getMessage(reply_msid)

	return send_answer(ev, core.muzzle_user(c_user, cm.user_id, toMuzzle=False), reply_to=PARENT)


@takesArgument(optional=True, isUsername=True)
def cmd_cleanup(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.cleanup_user(c_user, arg))

	if ev.reply_to_message is None:
		return send_answer(ev, rp.Reply(rp.types.ERR_NO_REPLY), reply_to=EVENT)

	user_id = getUserIdFromReply(ev)

	return send_answer(ev, core.cleanup_user(c_user, user_id), reply_to=EVENT)

@takesArgument(optional=True)
def cmd_lockdown(ev, arg):
	c_user = UserContainer(ev.from_user)
	if arg:
		return send_answer(ev, core.engage_lockdown(c_user, arg))
	return send_answer(ev, core.engage_lockdown(c_user), reply_to=EVENT)

def cmd_mute(ev):
	global mute
	user = db.getUser(ev.from_user.id)
	if not user or user.rank < RANKS.admin:
		return
	mute = not mute
	for admin in db.iterateAdmins():
		if mute:
			core._push_system_message(rp.Reply(rp.types.CUSTOM, text="<i>Non-admins have been muted.</i>"), who=admin)
		else:
			core._push_system_message(rp.Reply(rp.types.CUSTOM, text="<i>Non-admin voices have been restored.</i>"), who=admin)

cmd_t = cmd_tsign # alias
cmd_fetch = cmd_info # alias

cmd_ban = cmd_blacklist # alias
cmd_unban = cmd_unblacklist # alias

cmd_rules = cmd_motd # alias #FIX: make a secondary MOTD-like thing in the DB for rules.

# FIX: Make the command results reply to the message instead of to your command, if there's a reply_msid.



# @takesArgument()
# def cmd_me(ev, arg):
# 	user = db.getUser(id=ev.from_user.id)
# 	ev.text = "<i>*anon "+arg.replace("<","&lt;").replace(">","&gt;")+"*</i>"
# 	if ev.reply_to_message is not None:
# 		reply_msid = ch.lookupMapping(ev.from_user.id, data=ev.reply_to_message.message_id)

# 		if reply_msid is None:
# 			return send_answer(ev, rp.Reply(rp.types.ERR_NOT_IN_CACHE), reply_to=EVENT)
# 		core._push_system_message(rp.Reply(rp.types.CUSTOM,text=ev.text), reply_to=reply_msid, except_who=user)

# 	core._push_system_message(rp.Reply(rp.types.CUSTOM,text=ev.text),except_who=user)
# 	msid = ch.assignMessageId(ev.message_id)
# 	ch.saveMapping(user.id, msid, ev.message_id)
