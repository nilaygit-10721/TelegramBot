from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, ConversationHandler
from openai import OpenAI
import os
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import time ,datetime, timedelta
import random
import logging
import json
import asyncio
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# Load environment variables
load_dotenv()
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import os
import json

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


 #Initialize Firebase with error handling
FIRESTORE_AVAILABLE = False

def initialize_firebase():
    global FIRESTORE_AVAILABLE, db
    
    try:
        # Get the service account JSON from environment variable
        service_account_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
        
        # Check if the environment variable exists
        if not service_account_json:
            logger.error("FIREBASE_SERVICE_ACCOUNT_JSON not set in .env file")
            return False
            
        # Parse the JSON string to a dictionary
        try:
            service_account_info = json.loads(service_account_json)
        except json.JSONDecodeError:
            logger.error("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON")
            return False
            
        # Initialize the app
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        
        # Initialize Firestore
        db = firestore.client()
        
        # Test the connection
        test_ref = db.collection('test').document('connection_test')
        test_ref.set({'timestamp': firestore.SERVER_TIMESTAMP})
        test_doc = test_ref.get()
        
        logger.info(f"Firebase initialized successfully. Test data: {test_doc.to_dict()}")
        return True
        
    except Exception as e:
        logger.error(f"Error initializing Firebase: {e}")
        return False

# Initialize Firebase
FIRESTORE_AVAILABLE = initialize_firebase()

# Initialize OpenAI API
API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL")
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Core Features ---
# 1. Start Command (Onboarding New Users)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    user_data = {
        "user_id": user_id,
        "user_name": user_name,
        "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "points": 0,  # Initialize points for gamification
        "streak": 0,  # Initialize streak for daily engagement tracking
        "last_active": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "interests": []  # For personalized content
    }
    
    # Check if user already exists, if not create new document
    user_ref = db.collection("users").document(str(user_id))
    if not user_ref.get().exists:
        user_ref.set(user_data)
    
    # Create welcome message with buttons
    keyboard = [
        [InlineKeyboardButton("📚 Learn About Us", callback_data="about")],
        [InlineKeyboardButton("🎯 Set Interests", callback_data="interests")],
        [InlineKeyboardButton("🤖 AI Chat", callback_data="chat")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 Welcome {user_name} to Systemic Altruism's AI-Powered Bot! \n\n"
        "I'm here to help you engage with our community and provide valuable information.\n\n"
        "What would you like to do today?",
        reply_markup=reply_markup
    )

# 2. AI-Powered Responses
async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    user_id = update.message.from_user.id
    
    # Update last active timestamp and check for streaks
    await update_user_activity(user_id)
    
    try:
        # First, check if user's message matches any FAQ
        faq_response = await check_faqs(user_input)
        if faq_response:
            await update.message.reply_text(faq_response)
            return
            
        # Send typing indicator
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        # Get user's interests for context
        user_ref = db.collection("users").document(str(user_id))
        user_doc = user_ref.get()
        
        # Check if user exists in database
        interests_context = ""
        if user_doc.exists:
            user_data = user_doc.to_dict()
            interests = user_data.get("interests", [])
            if interests:
                interests_context = f"The user has expressed interest in: {', '.join(interests)}. "
        else:
            # Create a new user document if it doesn't exist
            new_user_data = {
                "user_id": user_id,
                "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "interests": [],
                "points": 0,
                "last_active": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            user_ref.set(new_user_data)
            
        # Personalized prompt based on user's chat history and interests
        system_prompt = f"You are an assistant for Systemic Altruism, an organization focused on effective altruism and community-building. {interests_context}Provide helpful, concise responses. Keep answers under 150 words unless detailed information is requested."
        
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ]
        )
        response_text = completion.choices[0].message.content
        
        # Add buttons for follow-up actions
        keyboard = [
            [InlineKeyboardButton("♻️ Regenerate Response", callback_data=f"regenerate_{user_input}")],
            [InlineKeyboardButton("📊 See Related Resources", callback_data="resources")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(response_text, reply_markup=reply_markup)
        
        # Log the chat in Firebase
        chat_data = {
            "user_id": user_id,
            "user_input": user_input,
            "bot_response": response_text,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        db.collection("chats").add(chat_data)
        
        # Add points for engagement
        user_ref.update({"points": firestore.Increment(1)})
        
    except Exception as e:
        await update.message.reply_text("❌ Error in AI response. Please try again later.")
        logging.error(f"Error in AI response: {e}")
# 3. Motivational Quotes Generator
quotes = [
    "🌟 Believe in yourself and all that you are.",
    "💪 Strength comes from the struggle.",
    "🚀 Dream big and dare to fail."
]

async def motivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(random.choice(quotes))

# 4. Admin Announcements
async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if str(user_id) == os.getenv("ADMIN_ID"):
        announcement = " ".join(context.args)
        if announcement:
            # Send announcement to all users
            users_ref = db.collection("users")
            docs = users_ref.stream()
            for doc in docs:
                user_data = doc.to_dict()
                try:
                    await context.bot.send_message(chat_id=user_data["user_id"], text=f"📢 Announcement: {announcement}")
                except Exception as e:
                    logging.error(f"Failed to send announcement to {user_data['user_id']}: {e}")
            await update.message.reply_text("Announcement sent to all users.")
        else:
            await update.message.reply_text("Please provide an announcement message.")
    else:
        await update.message.reply_text("❌ You are not authorized to use this command.")

# 5. Sentiment Analysis
async def sentiment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.replace("/sentiment", "").strip()
    if not user_input:
        await update.message.reply_text("Please provide text after the /sentiment command for analysis.")
        return
        
    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": f"Analyze the sentiment of this text and categorize it as positive, negative, or neutral. Provide a brief explanation why: {user_input}"}]
        )
        sentiment_analysis = completion.choices[0].message.content
        await update.message.reply_text(f"Sentiment Analysis: {sentiment_analysis}")
    except Exception as e:
        await update.message.reply_text("❌ Error in sentiment analysis. Please try again later.")
        logging.error(f"Error in sentiment analysis: {e}")

# 6. Gamification (Leaderboard)
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users_ref = db.collection("users")
    docs = users_ref.stream()
    leaderboard_data = []
    for doc in docs:
        user_data = doc.to_dict()
        user_name = user_data.get("user_name", "Anonymous")
        points = user_data.get("points", 0)
        streak = user_data.get("streak", 0)
        leaderboard_data.append((user_name, points, streak))
    
    leaderboard_data.sort(key=lambda x: (x[1], x[2]), reverse=True)
    leaderboard_text = "🏆 Community Leaderboard 🏆\n\n"
    for idx, (user_name, points, streak) in enumerate(leaderboard_data[:10], start=1):
        fire_emoji = "🔥" * min(streak, 5) if streak > 0 else ""
        leaderboard_text += f"{idx}. {user_name} - {points} pts {fire_emoji}\n"
    
    # Generate and send the leaderboard as an image
    image = await generate_leaderboard_image(leaderboard_data[:10])
    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=image, caption="🏆 Top Community Contributors 🏆")

# =================================================================
# NEW FEATURE 1: Advanced Event Management & Registration System
# =================================================================

# Event states for conversation handler
TITLE, DESCRIPTION, DATE, TIME, LOCATION, MAX_PARTICIPANTS, CONFIRMATION = range(7)

# Command to create a new event (admin only)
async def create_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if str(user_id) != os.getenv("ADMIN_ID"):
        await update.message.reply_text("❌ Only admins can create events.")
        return ConversationHandler.END
    
    await update.message.reply_text("Let's create a new event! 📅\n\nFirst, what's the title of the event?")
    return TITLE

async def event_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['event_title'] = update.message.text
    await update.message.reply_text("Great! Now provide a brief description of the event:")
    return DESCRIPTION

async def event_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['event_description'] = update.message.text
    await update.message.reply_text("When is the event? Please use format YYYY-MM-DD:")
    return DATE

async def event_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['event_date'] = update.message.text
    await update.message.reply_text("What time does it start? Please use format HH:MM (24-hour):")
    return TIME

async def event_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['event_time'] = update.message.text
    await update.message.reply_text("Where will the event be held?")
    return LOCATION

async def event_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['event_location'] = update.message.text
    await update.message.reply_text("What's the maximum number of participants? (Enter a number)")
    return MAX_PARTICIPANTS

async def event_max_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        max_participants = int(update.message.text)
        context.user_data['event_max_participants'] = max_participants
        
        # Show summary for confirmation
        await update.message.reply_text(
            f"📝 Event Summary:\n"
            f"Title: {context.user_data['event_title']}\n"
            f"Description: {context.user_data['event_description']}\n"
            f"Date: {context.user_data['event_date']}\n"
            f"Time: {context.user_data['event_time']}\n"
            f"Location: {context.user_data['event_location']}\n"
            f"Max Participants: {max_participants}\n\n"
            f"Is this correct? (yes/no)"
        )
        return CONFIRMATION
    except ValueError:
        await update.message.reply_text("Please enter a valid number for maximum participants.")
        return MAX_PARTICIPANTS

async def event_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() in ['yes', 'y']:
        # Create event in database
        event_data = {
            "title": context.user_data['event_title'],
            "description": context.user_data['event_description'],
            "date": context.user_data['event_date'],
            "time": context.user_data['event_time'],
            "location": context.user_data['event_location'],
            "max_participants": context.user_data['event_max_participants'],
            "participants": [],
            "waitlist": [],
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "created_by": update.message.from_user.id
        }
        
        event_ref = db.collection("events").add(event_data)
        event_id = event_ref[1].id
        
        # Create announcement for all users
        keyboard = [
            [InlineKeyboardButton("Register Now", callback_data=f"register_{event_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send announcement to all users
        users_ref = db.collection("users")
        docs = users_ref.stream()
        for doc in docs:
            user_data = doc.to_dict()
            try:
                await context.bot.send_message(
                    chat_id=user_data["user_id"],
                    text=f"🎉 New Event: {context.user_data['event_title']}\n\n"
                         f"📝 {context.user_data['event_description']}\n\n"
                         f"📅 {context.user_data['event_date']} at {context.user_data['event_time']}\n"
                         f"📍 {context.user_data['event_location']}\n\n"
                         f"Limited spots available! Register now:",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logging.error(f"Failed to send event notification to {user_data['user_id']}: {e}")
        
        await update.message.reply_text("✅ Event created and announced successfully!")
        
        # Schedule reminder 1 day before the event
        try:
            event_date = datetime.strptime(f"{context.user_data['event_date']} {context.user_data['event_time']}", "%Y-%m-%d %H:%M")
            reminder_date = event_date - timedelta(days=1)
            current_date = datetime.now()
            seconds_until_reminder = (reminder_date - current_date).total_seconds()
            
            if seconds_until_reminder > 0:
                context.job_queue.run_once(
                    send_event_reminder,
                    seconds_until_reminder,
                    data={"event_id": event_id},
                    name=f"event_reminder_{event_id}"
                )
        except Exception as e:
            logging.error(f"Failed to schedule reminder: {e}")
        
    else:
        await update.message.reply_text("Event creation cancelled. You can start over with /create_event")
    
    # Clear user data
    context.user_data.clear()
    return ConversationHandler.END

# List all upcoming events
async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    events_ref = db.collection("events")
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # Query events that haven't happened yet
    docs = events_ref.where("date", ">=", current_date).stream()
    
    events = []
    for doc in docs:
        event_data = doc.to_dict()
        event_data["id"] = doc.id
        events.append(event_data)
    
    # Sort by date
    events.sort(key=lambda x: x["date"] + x["time"])
    
    if not events:
        await update.message.reply_text("No upcoming events at the moment. Stay tuned!")
        return
    
    # Display events
    response = "📅 Upcoming Events:\n\n"
    for idx, event in enumerate(events, start=1):
        participants_count = len(event.get("participants", []))
        max_participants = event.get("max_participants", 0)
        availability = f"({participants_count}/{max_participants} participants)"
        
        keyboard = [
            [InlineKeyboardButton("Register", callback_data=f"register_{event['id']}")],
            [InlineKeyboardButton("Details", callback_data=f"event_details_{event['id']}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"{idx}. {event['title']} - {event['date']}\n"
            f"📍 {event['location']} at {event['time']}\n"
            f"👥 {availability}",
            reply_markup=reply_markup
        )

# Event registration callback
async def event_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("register_"):
        event_id = data.replace("register_", "")
        await register_for_event(event_id, user_id, query)
    elif data.startswith("event_details_"):
        event_id = data.replace("event_details_", "")
        await show_event_details(event_id, query)
    elif data == "my_events":
        await show_my_events(user_id, query)
    elif data.startswith("cancel_registration_"):
        event_id = data.replace("cancel_registration_", "")
        await cancel_registration(event_id, user_id, query)

async def register_for_event(event_id, user_id, query):
    event_ref = db.collection("events").document(event_id)
    event_doc = event_ref.get()
    
    if not event_doc.exists:
        await query.edit_message_text("This event no longer exists.")
        return
    
    event_data = event_doc.to_dict()
    participants = event_data.get("participants", [])
    waitlist = event_data.get("waitlist", [])
    max_participants = event_data.get("max_participants", 0)
    
    # Check if user is already registered
    if str(user_id) in participants:
        keyboard = [
            [InlineKeyboardButton("Cancel Registration", callback_data=f"cancel_registration_{event_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "You're already registered for this event! 🎉\n\n"
            f"See you on {event_data.get('date')} at {event_data.get('time')}!",
            reply_markup=reply_markup
        )
        return
    
    # Check if user is on waitlist
    if str(user_id) in waitlist:
        await query.edit_message_text(
            "You're currently on the waitlist for this event. We'll notify you if a spot becomes available."
        )
        return
    
    # Check if event is full
    if len(participants) >= max_participants:
        # Add to waitlist
        event_ref.update({
            "waitlist": firestore.ArrayUnion([str(user_id)])
        })
        
        await query.edit_message_text(
            "This event is currently full. You've been added to the waitlist and will be notified if a spot becomes available."
        )
        return
    
    # Register the user
    event_ref.update({
        "participants": firestore.ArrayUnion([str(user_id)])
    })
    
    # Add calendar reminder button
    event_date = f"{event_data.get('date')}T{event_data.get('time')}:00"
    calendar_url = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={event_data.get('title')}&details={event_data.get('description')}&location={event_data.get('location')}&dates={event_date.replace('-', '')}%2F{event_date.replace('-', '')}"
    
    keyboard = [
        [InlineKeyboardButton("Add to Calendar", url=calendar_url)],
        [InlineKeyboardButton("Cancel Registration", callback_data=f"cancel_registration_{event_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"✅ You're registered for {event_data.get('title')}!\n\n"
        f"📅 {event_data.get('date')} at {event_data.get('time')}\n"
        f"📍 {event_data.get('location')}\n\n"
        "We'll send you a reminder before the event.",
        reply_markup=reply_markup
    )
    
    # Add points for registration
    user_ref = db.collection("users").document(str(user_id))
    user_ref.update({"points": firestore.Increment(5)})

async def show_event_details(event_id, query):
    event_ref = db.collection("events").document(event_id)
    event_doc = event_ref.get()
    
    if not event_doc.exists:
        await query.edit_message_text("This event no longer exists.")
        return
    
    event_data = event_doc.to_dict()
    participants = event_data.get("participants", [])
    waitlist = event_data.get("waitlist", [])
    max_participants = event_data.get("max_participants", 0)
    
    # Format participants list
    participant_names = []
    for p_id in participants:
        user_doc = db.collection("users").document(p_id).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            participant_names.append(user_data.get("user_name", "Unknown"))
    
    participant_text = ", ".join(participant_names) if participant_names else "No participants yet"
    
    keyboard = [
        [InlineKeyboardButton("Register", callback_data=f"register_{event_id}")],
        [InlineKeyboardButton("Back to Events", callback_data="list_events")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📝 {event_data.get('title')}\n\n"
        f"{event_data.get('description')}\n\n"
        f"📅 {event_data.get('date')} at {event_data.get('time')}\n"
        f"📍 {event_data.get('location')}\n\n"
        f"👥 Participants ({len(participants)}/{max_participants}):\n{participant_text}",
        reply_markup=reply_markup
    )

async def show_my_events(user_id, query):
    events_ref = db.collection("events")
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    # Get all upcoming events
    events = []
    for doc in events_ref.where("date", ">=", current_date).stream():
        event_data = doc.to_dict()
        event_data["id"] = doc.id
        
        # Check if user is registered
        participants = event_data.get("participants", [])
        waitlist = event_data.get("waitlist", [])
        
        if str(user_id) in participants:
            event_data["status"] = "registered"
            events.append(event_data)
        elif str(user_id) in waitlist:
            event_data["status"] = "waitlist"
            events.append(event_data)
    
    if not events:
        await query.edit_message_text(
            "You're not registered for any upcoming events. Use /events to see available events."
        )
        return
    
    # Sort by date
    events.sort(key=lambda x: x["date"] + x["time"])
    
    response = "🗓 Your Upcoming Events:\n\n"
    for idx, event in enumerate(events, start=1):
        status = "✅ Registered" if event["status"] == "registered" else "⏳ On Waitlist"
        
        response += f"{idx}. {event['title']} - {event['date']}\n"
        response += f"    📍 {event['location']} at {event['time']}\n"
        response += f"    Status: {status}\n\n"
    
    keyboard = [
        [InlineKeyboardButton("View All Events", callback_data="list_events")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(response, reply_markup=reply_markup)

async def cancel_registration(event_id, user_id, query):
    event_ref = db.collection("events").document(event_id)
    event_doc = event_ref.get()
    
    if not event_doc.exists:
        await query.edit_message_text("This event no longer exists.")
        return
    
    event_data = event_doc.to_dict()
    participants = event_data.get("participants", [])
    waitlist = event_data.get("waitlist", [])
    
    # Check if user is registered
    if str(user_id) not in participants:
        await query.edit_message_text("You're not registered for this event.")
        return
    
    # Remove user from participants
    event_ref.update({
        "participants": firestore.ArrayRemove([str(user_id)])
    })
    
    # Check if there's someone on the waitlist to fill the spot
    if waitlist:
        next_participant = waitlist[0]
        event_ref.update({
            "participants": firestore.ArrayUnion([next_participant]),
            "waitlist": firestore.ArrayRemove([next_participant])
        })
        
        # Notify the person who got moved from waitlist
        try:
            user_doc = db.collection("users").document(next_participant).get()
            if user_doc.exists:
                await context.bot.send_message(
                    chat_id=int(next_participant),
                    text=f"🎉 Good news! A spot has opened up for '{event_data.get('title')}' on {event_data.get('date')}. You've been automatically registered!"
                )
        except Exception as e:
            logging.error(f"Failed to notify waitlisted user: {e}")
    
    await query.edit_message_text(
        f"✅ You've been removed from '{event_data.get('title')}' on {event_data.get('date')}."
    )

# Send event reminder
async def send_event_reminder(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    event_id = job_data.get("event_id")
    
    event_ref = db.collection("events").document(event_id)
    event_doc = event_ref.get()
    
    if not event_doc.exists:
        return
    
    event_data = event_doc.to_dict()
    participants = event_data.get("participants", [])
    
    for participant_id in participants:
        try:
            await context.bot.send_message(
                chat_id=int(participant_id),
                text=f"⏰ Reminder: '{event_data.get('title')}' is tomorrow at {event_data.get('time')}!\n\n"
                     f"📍 Location: {event_data.get('location')}\n\n"
                     f"We're looking forward to seeing you there!"
            )
        except Exception as e:
            logging.error(f"Failed to send reminder to {participant_id}: {e}")

# =================================================================
# NEW FEATURE 2: AI-Powered Content Personalization System
# =================================================================

# Interest selection
SELECTING_INTERESTS = 0

# Interest categories
INTEREST_CATEGORIES = {
    "topics": ["Effective Altruism", "Climate Change", "Global Health", "Animal Welfare", "AI Safety", "Poverty", "Education"],
    "content_types": ["Articles", "Research", "Case Studies", "Events", "Discussions", "Projects", "News"],
    "engagement_level": ["Beginner", "Intermediate", "Advanced", "Professional"]
}

async def set_interests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    # Create keyboard with interest categories
    keyboard = []
    for category, interests in INTEREST_CATEGORIES.items():
        keyboard.append([InlineKeyboardButton(f"{category.replace('_', ' ').title()}", callback_data=f"interest_category_{category}")])
    
    keyboard.append([InlineKeyboardButton("View My Interests", callback_data="view_interests")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "What kind of content are you interested in? Select a category:",
        reply_markup=reply_markup
    )
    return SELECTING_INTERESTS

async def interests_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    if data.startswith("interest_category_"):
        category = data.replace("interest_category_", "")
        keyboard = []
        
        # Show interests for the selected category
        for interest in INTEREST_CATEGORIES[category]:
            keyboard.append([InlineKeyboardButton(interest, callback_data=f"select_interest_{interest}")])
        
        keyboard.append([InlineKeyboardButton("« Back to Categories", callback_data="back_to_categories")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"Select your interests in {category.replace('_', ' ')}:",
            reply_markup=reply_markup
        )
        return SELECTING_INTERESTS
    
    elif data.startswith("select_interest_"):
        interest = data.replace("select_interest_", "")
        user_ref = db.collection("users").document(str(user_id))
        
        # Add interest to user profile
        user_ref.update({
            "interests": firestore.ArrayUnion([interest])
        })
        
        await query.edit_message_text(
            f"✅ Added '{interest}' to your interests!\n\nWhat else are you interested in?",
            reply_markup=query.message.reply_markup
        )
        return SELECTING_INTERESTS
    
    elif data == "back_to_categories":
        # Return to categories selection
        keyboard = []
        for category, interests in INTEREST_CATEGORIES.items():
            keyboard.append([InlineKeyboardButton(f"{category.replace('_', ' ').title()}", callback_data=f"interest_category_{category}")])
        
        keyboard.append([InlineKeyboardButton("View My Interests", callback_data="view_interests")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "What kind of content are you interested in? Select a category:",
            reply_markup=reply_markup
        )
        return SELECTING_INTERESTS
    
    elif data == "view_interests":
        # Show user's current interests
        user_ref = db.collection("users").document(str(user_id))
        user_doc = user_ref.get()
        user_data = user_doc.to_dict()
        interests = user_data.get("interests", [])
        
        if not interests:
            message = "You haven't set any interests yet. Select a category to add interests!"
        else:
            message = "Your current interests:\n\n" + "\n".join([f"• {interest}" for interest in interests])
            
        keyboard = [
            [InlineKeyboardButton("Add More Interests", callback_data="back_to_categories")],
            [InlineKeyboardButton("Clear All Interests", callback_data="clear_interests")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(message, reply_markup=reply_markup)
        return SELECTING_INTERESTS
    
    elif data == "clear_interests":
        # Clear all interests
        user_ref = db.collection("users").document(str(user_id))
        user_ref.update({"interests": []})
        
        keyboard = []
        for category, interests in INTEREST_CATEGORIES.items():
            keyboard.append([InlineKeyboardButton(f"{category.replace('_', ' ').title()}", callback_data=f"interest_category_{category}")])
        
        keyboard.append([InlineKeyboardButton("View My Interests", callback_data="view_interests")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "✅ All interests cleared. What would you like to explore?",
            reply_markup=reply_markup
        )
        return SELECTING_INTERESTS
    
    return ConversationHandler.END

# Generate personalized daily content digest
async def daily_digest(context: ContextTypes.DEFAULT_TYPE):
    users_ref = db.collection("users")
    
    for user_doc in users_ref.stream():
        user_data = user_doc.to_dict()
        user_id = user_data.get("user_id")
        user_name = user_data.get("user_name", "there")
        interests = user_data.get("interests", [])
        
        # Skip users with no interests
        if not interests:
            continue
        
        try:
            # Generate personalized digest based on interests
            prompt = f"Create a short daily digest for a community member interested in {', '.join(interests)}. Include 2-3 brief updates related to their interests in effective altruism. Format it in a conversational, engaging way that fits in a Telegram message. Include a unique, thoughtful question at the end to encourage engagement."
            
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}]
            )
            digest_content = completion.choices[0].message.content
            
            # Add personalized greeting
            digest_message = f"Good morning, {user_name}! 🌞\n\nHere's your personalized daily digest:\n\n{digest_content}\n\nReply to this message to share your thoughts!"
            
            # Create action buttons
            keyboard = [
                [InlineKeyboardButton("🔍 Explore More", callback_data="explore_more")],
                [InlineKeyboardButton("💬 Join Discussion", callback_data="join_discussion")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=user_id,
                text=digest_message,
                reply_markup=reply_markup
            )
            
            # Update last digest timestamp
            db.collection("users").document(str(user_id)).update({
                "last_digest": datetime.now().strftime("%Y-%m-%d")
            })
            
        except Exception as e:
            logging.error(f"Failed to send digest to user {user_id}: {e}")

# Generate and send personalized content recommendation
async def recommend_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_ref = db.collection("users").document(str(user_id))
    user_doc = user_ref.get()
    
    if not user_doc.exists:
        await update.message.reply_text("Please use /start first to set up your profile!")
        return
        
    user_data = user_doc.to_dict()
    interests = user_data.get("interests", [])
    
    if not interests:
        keyboard = [
            [InlineKeyboardButton("Set Interests", callback_data="set_interests")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "I don't know your interests yet! Set your interests first so I can recommend content:",
            reply_markup=reply_markup
        )
        return
    
    # Get user's chat history for context
    chats_ref = db.collection("chats").where("user_id", "==", user_id).order_by("timestamp", direction=firestore.Query.DESCENDING).limit(5)
    recent_chats = []
    for chat in chats_ref.stream():
        chat_data = chat.to_dict()
        recent_chats.append(chat_data.get("user_input", ""))
    
    # Send typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    try:
        # Generate personalized recommendations
        prompt = f"""
        Generate personalized content recommendations for a community member.
        
        Their interests include: {', '.join(interests)}
        
        Recent conversation topics: {', '.join(recent_chats) if recent_chats else 'No recent conversations'}
        
        Provide 3 specific, curated resources they might find valuable. For each recommendation, include:
        1. A descriptive title
        2. A 1-2 sentence explanation of why it's relevant to their interests
        3. A brief teaser about what they'll learn
        
        Format each recommendation clearly. Make recommendations specific rather than generic. Focus on effective altruism, community building, and related topics.
        """
        
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are a recommendation engine for an effective altruism community. Provide specific, personalized recommendations based on user interests."},
                      {"role": "user", "content": prompt}]
        )
        recommendations = completion.choices[0].message.content
        
        await update.message.reply_text(
            f"📚 Here are some personalized recommendations based on your interests:\n\n{recommendations}\n\n"
            "Would you like me to recommend more content like this? Use /recommend anytime!"
        )
        
    except Exception as e:
        await update.message.reply_text("❌ Error generating recommendations. Please try again later.")
        logging.error(f"Error in recommendations: {e}")

# =================================================================
# NEW FEATURE 3: Interactive AI-Powered Community Insights Dashboard
# =================================================================

# Generate community insights
async def community_insights(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    admin_id = os.getenv("ADMIN_ID")
    
    # Basic insights for regular users, detailed for admins
    is_admin = str(user_id) == admin_id
    
    # Send typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    try:
        # Collect community data
        users_ref = db.collection("users")
        chats_ref = db.collection("chats")
        events_ref = db.collection("events")
        
        # Count total users
        total_users = len(list(users_ref.stream()))
        
        # Count active users in last 7 days
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        active_users = 0
        for user in users_ref.stream():
            user_data = user.to_dict()
            last_active = user_data.get("last_active", "")
            if last_active >= seven_days_ago:
                active_users += 1
        
        # Count total conversations
        total_chats = len(list(chats_ref.stream()))
        
        # Count upcoming events
        current_date = datetime.now().strftime("%Y-%m-%d")
        upcoming_events = len(list(events_ref.where("date", ">=", current_date).stream()))
        
        # Get top interests in the community
        interest_counts = {}
        for user in users_ref.stream():
            user_data = user.to_dict()
            interests = user_data.get("interests", [])
            for interest in interests:
                interest_counts[interest] = interest_counts.get(interest, 0) + 1
        
        # Sort interests by popularity
        sorted_interests = sorted(interest_counts.items(), key=lambda x: x[1], reverse=True)
        top_interests = sorted_interests[:5] if sorted_interests else []
        
        # Generate insights dashboard image
        dashboard_image = await generate_insights_image(
            total_users, active_users, total_chats, upcoming_events, top_interests
        )
        
        # Base message for all users
        message = f"📊 Community Insights\n\n"
        message += f"👥 Total Members: {total_users}\n"
        message += f"🟢 Active Members (7 days): {active_users}\n"
        message += f"📅 Upcoming Events: {upcoming_events}\n\n"
        
        if top_interests:
            message += "Top Community Interests:\n"
            for interest, count in top_interests:
                percentage = (count / total_users) * 100
                message += f"• {interest}: {percentage:.1f}%\n"
        
        # Extra insights for admins
        if is_admin:
            # Analyze weekly engagement trend
            now = datetime.now()
            weekly_chats = []
            
            for i in range(4):  # Last 4 weeks
                start_date = (now - timedelta(days=(i+1)*7)).strftime("%Y-%m-%d")
                end_date = (now - timedelta(days=i*7)).strftime("%Y-%m-%d")
                
                week_chats = len(list(chats_ref.where("timestamp", ">=", start_date).where("timestamp", "<", end_date).stream()))
                weekly_chats.append((f"Week {4-i}", week_chats))
            
            # Analyze trending topics using AI
            recent_chats_query = chats_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(50)
            recent_messages = []
            
            for chat in recent_chats_query.stream():
                chat_data = chat.to_dict()
                recent_messages.append(chat_data.get("user_input", ""))
            
            if recent_messages:
                prompt = f"""
                Analyze these recent community conversations and identify:
                1. Top 3-5 trending topics or themes
                2. Any emerging questions or concerns
                3. General sentiment (positive, neutral, negative)
                
                Messages to analyze:
                {recent_messages[:50]}
                
                Provide a concise summary suitable for community managers.
                """
                
                completion = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "system", "content": "You are an analytics assistant for community managers."},
                              {"role": "user", "content": prompt}]
                )
                ai_insights = completion.choices[0].message.content
                
                admin_message = message + "\n\n🔍 Advanced Insights:\n\n" + ai_insights
                
                # Create admin action buttons
                keyboard = [
                    [InlineKeyboardButton("📣 Send Community Update", callback_data="send_community_update")],
                    [InlineKeyboardButton("📊 Export Full Report", callback_data="export_insights")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Send the image with admin message
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=dashboard_image,
                    caption=admin_message[:1024],  # Telegram caption limit
                    reply_markup=reply_markup
                )
                
                # If the message is too long, send the rest separately
                if len(admin_message) > 1024:
                    await update.message.reply_text(admin_message[1024:])
            else:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=dashboard_image,
                    caption=message
                )
        else:
            # For regular users, just send the basic insights with the image
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=dashboard_image,
                caption=message
            )
        
    except Exception as e:
        await update.message.reply_text("❌ Error generating community insights. Please try again later.")
        logging.error(f"Error in community insights: {e}")

# Generate insights visualization
async def generate_insights_image(total_users, active_users, total_chats, upcoming_events, top_interests):
    # Create a new image with gradient background
    width, height = 800, 600
    img = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    
    # Draw gradient background
    for y in range(height):
        r = int(53 + (y / height) * 30)
        g = int(106 + (y / height) * 30)
        b = int(164 + (y / height) * 20)
        for x in range(width):
            draw.point((x, y), fill=(r, g, b))
    
    # Try to load a font, use default if not available
    try:
        title_font = ImageFont.truetype("arial.ttf", 36)
        header_font = ImageFont.truetype("arial.ttf", 28)
        body_font = ImageFont.truetype("arial.ttf", 24)
    except IOError:
        title_font = ImageFont.load_default()
        header_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
    
    # Draw title
    title = "Community Insights Dashboard"
    title_width = draw.textlength(title, font=title_font)
    draw.text(((width-title_width)/2, 30), title, font=title_font, fill=(255, 255, 255))
    
    # Draw date
    date_text = datetime.now().strftime("%B %d, %Y")
    date_width = draw.textlength(date_text, font=body_font)
    draw.text(((width-date_width)/2, 80), date_text, font=body_font, fill=(255, 255, 255))
    
    # Draw key metrics in boxes
    metrics = [
        {"label": "Members", "value": str(total_users), "icon": "👥"},
        {"label": "Active Users", "value": str(active_users), "icon": "🟢"},
        {"label": "Total Chats", "value": str(total_chats), "icon": "💬"},
        {"label": "Upcoming Events", "value": str(upcoming_events), "icon": "📅"}
    ]
    
    box_width = 160
    box_height = 120
    box_margin = 20
    
    # Calculate total width of all boxes and starting x position
    total_box_width = (box_width + box_margin) * len(metrics) - box_margin
    start_x = (width - total_box_width) / 2
    
    for i, metric in enumerate(metrics):
        box_x = start_x + i * (box_width + box_margin)
        box_y = 140
        
        # Draw box with rounded corners
        draw.rectangle([(box_x, box_y), (box_x+box_width, box_y+box_height)], 
                      fill=(255, 255, 255, 230), outline=(255, 255, 255))
        
        # Draw icon and values
        icon_text = metric["icon"]
        icon_width = draw.textlength(icon_text, font=header_font)
        draw.text((box_x + (box_width-icon_width)/2, box_y + 10), icon_text, font=header_font, fill=(53, 106, 164))
        
        value_text = metric["value"]
        value_width = draw.textlength(value_text, font=header_font)
        draw.text((box_x + (box_width-value_width)/2, box_y + 45), value_text, font=header_font, fill=(33, 33, 33))
        
        label_text = metric["label"]
        label_width = draw.textlength(label_text, font=body_font)
        draw.text((box_x + (box_width-label_width)/2, box_y + 80), label_text, font=body_font, fill=(100, 100, 100))
    
    # Draw community interests chart
    if top_interests:
        # Header for the interests section
        interests_title = "Top Community Interests"
        interests_title_width = draw.textlength(interests_title, font=header_font)
        draw.text(((width-interests_title_width)/2, 300), interests_title, font=header_font, fill=(255, 255, 255))
        
        # Draw horizontal bar chart
        chart_left = 150
        chart_right = width - 150
        chart_top = 350
        bar_height = 30
        bar_margin = 15
        bar_width = chart_right - chart_left
        
        for i, (interest, count) in enumerate(top_interests):
            percentage = count / total_users
            
            y_position = chart_top + i * (bar_height + bar_margin)
            
            # Draw label
            draw.text((chart_left - 10, y_position), interest, font=body_font, fill=(255, 255, 255), anchor="re")
            
            # Draw bar background
            draw.rectangle([(chart_left, y_position), (chart_right, y_position + bar_height)], 
                          fill=(255, 255, 255, 100), outline=(255, 255, 255))
            
            # Draw filled portion of bar
            filled_width = int(bar_width * percentage)
            draw.rectangle([(chart_left, y_position), (chart_left + filled_width, y_position + bar_height)], 
                          fill=(255, 255, 255, 230), outline=None)
            
            # Draw percentage
            percent_text = f"{percentage*100:.1f}%"
            draw.text((chart_left + filled_width + 10, y_position + bar_height/2), 
                     percent_text, font=body_font, fill=(255, 255, 255))
    
    # Convert to BytesIO for sending
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return img_byte_arr

# Generate leaderboard image
async def generate_leaderboard_image(leaderboard_data):
    # Create a new image with gradient background
    width, height = 800, 600
    img = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    
    # Draw gradient background
    for y in range(height):
        r = int(53 + (y / height) * 30)
        g = int(106 + (y / height) * 30)
        b = int(164 + (y / height) * 20)
        for x in range(width):
            draw.point((x, y), fill=(r, g, b))
    
    # Try to load a font, use default if not available
    try:
        title_font = ImageFont.truetype("arial.ttf", 36)
        header_font = ImageFont.truetype("arial.ttf", 28)
        body_font = ImageFont.truetype("arial.ttf", 24)
    except IOError:
        title_font = ImageFont.load_default()
        header_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
    
    # Draw title
    title = "Community Leaderboard"
    title_width = draw.textlength(title, font=title_font)
    draw.text(((width-title_width)/2, 30), title, font=title_font, fill=(255, 255, 255))
    
    # Draw medal emojis and player stats
    medals = ["🥇", "🥈", "🥉"]
    
    for idx, (user_name, points, streak) in enumerate(leaderboard_data):
        y_position = 120 + idx * 45
        
        # Draw rank
        rank_text = f"{idx+1}."
        if idx < 3:
            rank_text = f"{medals[idx]}"
        
        draw.text((50, y_position), rank_text, font=header_font, fill=(255, 255, 255))
        
        # Draw username
        username_text = user_name
        if len(username_text) > 20:
            username_text = username_text[:18] + "..."
        draw.text((120, y_position), username_text, font=body_font, fill=(255, 255, 255))
        
        # Draw points
        points_text = f"{points} pts"
        points_width = draw.textlength(points_text, font=body_font)
        draw.text((width-200, y_position), points_text, font=body_font, fill=(255, 255, 255))
        
        # Draw streak if exists
        if streak > 0:
            streak_text = f"🔥 {streak}"
            streak_width = draw.textlength(streak_text, font=body_font)
            draw.text((width-80, y_position), streak_text, font=body_font, fill=(255, 255, 255))
    
    # Draw decorative elements
    draw.text((width/2, height-80), "Keep engaging to earn more points!", font=body_font, fill=(255, 255, 255), anchor="mm")
    draw.text((width/2, height-40), "Updated daily", font=body_font, fill=(200, 200, 200), anchor="mm")
    
    # Convert to BytesIO for sending
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return img_byte_arr

# Check FAQs for quick responses
async def check_faqs(user_input):
    # Simple FAQ matching
    faqs = {
        "what is this bot": "This is an AI-powered community management bot for Systemic Altruism. I can help with answering questions, providing resources, managing events, and more!",
        "how do i register for events": "You can see upcoming events with the /events command and register directly through the buttons provided.",
        "what is effective altruism": "Effective Altruism is a philosophy and social movement that applies evidence and reason to determine the most effective ways to benefit others.",
        "how do i earn points": "You earn points by engaging with the bot, participating in discussions, attending events, and completing community activities!",
        "how can i contribute": "There are many ways to contribute! You can participate in events, share resources, engage in discussions, or volunteer for projects. Use /volunteer to learn more.",
    }
    
    # Check for FAQ matches (simple substring matching)
    user_input_lower = user_input.lower()
    for key, value in faqs.items():
        if key in user_input_lower:
            return value
    
    return None

# Update user activity and manage streaks
async def update_user_activity(user_id):
    user_ref = db.collection("users").document(str(user_id))
    user_doc = user_ref.get()
    
    if user_doc.exists:
        user_data = user_doc.to_dict()
        last_active_str = user_data.get("last_active", "")
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # Skip if already active today
        if last_active_str.startswith(current_date):
            return
        
        # Check if maintaining streak (active yesterday)
        try:
            last_active_date = datetime.strptime(last_active_str.split(" ")[0], "%Y-%m-%d")
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            yesterday_date = datetime.strptime(yesterday, "%Y-%m-%d")
            
            if last_active_date.date() == yesterday_date.date():
                # Maintain streak
                user_ref.update({
                    "streak": firestore.Increment(1),
                    "last_active": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                
                # Check for streak milestones
                current_streak = user_data.get("streak", 0) + 1
                if current_streak in [7, 30, 100]:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🎯 Congratulations! You've maintained a {current_streak}-day streak! Keep up the great work!"
                    )
            else:
                # Reset streak
                user_ref.update({
                    "streak": 1,
                    "last_active": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
        except:
            # If there's any error, just update the last active time
            user_ref.update({
                "last_active": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })

# --- Command to send feedback to admins ---
async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    
    # Check if there's feedback text
    if not context.args:
        await update.message.reply_text("Please provide your feedback after the command. For example: /feedback I love this bot!")
        return
    
    feedback_text = " ".join(context.args)
    
    # Store feedback in database
    feedback_data = {
        "user_id": user_id,
        "user_name": user_name,
        "feedback": feedback_text,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "new"
    }
    db.collection("feedback").add(feedback_data)
    
    # Forward to admin
    admin_id = os.getenv("ADMIN_ID")
    if admin_id:
        try:
            await context.bot.send_message(
                chat_id=int(admin_id),
                text=f"📝 New Feedback from {user_name} (ID: {user_id}):\n\n\"{feedback_text}\""
            )
        except Exception as e:
            logging.error(f"Failed to send feedback to admin: {e}")
    
    await update.message.reply_text("Thank you for your feedback! We appreciate your input and will use it to improve the bot.")

# --- Bot Setup ---
app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

# Register conversation handlers
interest_handler = ConversationHandler(
    entry_points=[CommandHandler("interests", set_interests)],
    states={
        SELECTING_INTERESTS: [CallbackQueryHandler(interests_callback)]
    },
    fallbacks=[CommandHandler("cancel", lambda update, context: ConversationHandler.END)]
)

event_creation_handler = ConversationHandler(
    entry_points=[CommandHandler("create_event", create_event)],
    states={
        TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_title)],
        DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_description)],
        DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_date)],
        TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_time)],
        LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_location)],
        MAX_PARTICIPANTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_max_participants)],
        CONFIRMATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_confirmation)]
    },
    fallbacks=[CommandHandler("cancel", lambda update, context: ConversationHandler.END)]
)

# Command Handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("motivate", motivate))
app.add_handler(CommandHandler("announce", announce))
app.add_handler(CommandHandler("sentiment", sentiment))
app.add_handler(CommandHandler("leaderboard", leaderboard))
app.add_handler(CommandHandler("events", list_events))
app.add_handler(CommandHandler("insights", community_insights))
app.add_handler(CommandHandler("recommend", recommend_content))
app.add_handler(CommandHandler("feedback", feedback))

# Conversation Handlers
app.add_handler(interest_handler)
app.add_handler(event_creation_handler)

# Callback Query Handlers
app.add_handler(CallbackQueryHandler(event_callback, pattern="^(register_|event_details_|cancel_registration_)"))

# Message Handlers
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_reply))

# Schedule daily jobs
job_queue = app.job_queue
job_queue.run_daily(daily_digest, time=time(hour=8, minute=0, second=0))  # 8 AM daily digest

print("Enhanced Bot is running... 🚀")
app.run_polling()
        