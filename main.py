from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import firebase_admin
from firebase_admin import credentials, firestore
import uuid
import random
from datetime import datetime, timedelta

# Initialize FastAPI
app = FastAPI(title="Void Backend API")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # This allows your live website to connect!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Firebase Admin SDK
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Pydantic Models
class MessageData(BaseModel):
    text: str
    tags: List[str]
    ghost_id: str = None       # Added = None so it becomes optional
    timestamp: datetime = None # Added = None so it becomes optional
    self_destruct: bool = True 

class ReplyData(BaseModel):
    message_id: str
    text: str
    ghost_id: str
    timestamp: datetime = None

class PrivateMessage(BaseModel):
    sender_id: str
    receiver_id: str
    text: str
    timestamp: str

# Generate Ghost ID
def generate_ghost_id() -> str:
    return str(uuid.uuid4())[:8].upper()

# Set self-destruct time (24 hours from now)
def set_self_destruct_time() -> datetime:
    return datetime.utcnow() + timedelta(hours=24)

# --- ROUTES ---

@app.post("/send")
async def send_message(message: MessageData):
    """Save a new message to Firestore. Selects 10 random recipients."""
    try:
        ghost_id = message.ghost_id or generate_ghost_id()
        self_destruct_time = set_self_destruct_time()
        
        # Get all existing messages to select random recipients
        messages_ref = db.collection("messages")
        all_messages = messages_ref.stream()
        
        # Grab actual Ghost IDs from the database
        all_ghost_ids = []
        for msg in all_messages:
            data = msg.to_dict()
            if "ghost_id" in data:
                all_ghost_ids.append(data["ghost_id"])
                
        all_ghost_ids = list(set(all_ghost_ids))
        
        # Select random recipients (strictly excluding the sender)
        available_ids = [gid for gid in all_ghost_ids if gid != ghost_id]
        if len(available_ids) < 10:
            recipients = available_ids[:min(10, len(available_ids))]
        else:
            recipients = random.sample(available_ids, 10)
        
        # Create message document
        message_data = {
            "text": message.text,
            "tags": message.tags,
            "ghost_id": ghost_id,
            "timestamp": message.timestamp,
            "recipients": recipients,
            "self_destruct": message.self_destruct,
            "self_destruct_time": self_destruct_time,
            "replies": []
        }
        
        doc_ref = messages_ref.add(message_data)
        print(f"Message saved with ID: {doc_ref[1].id}")
        
        return {
            "success": True,
            "message_id": doc_ref[1].id,
            "ghost_id": ghost_id,
            "recipients_count": len(recipients)
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/reply")
async def send_reply(reply: ReplyData):
    """Save a reply to an existing message in Firestore."""
    try:
        # Find the specific message in the database
        msg_ref = db.collection("messages").document(reply.message_id)
        
        # Package the reply
        reply_data = {
            "text": reply.text,
            "ghost_id": reply.ghost_id,
            "timestamp": reply.timestamp or datetime.utcnow()
        }
        
        # Add it to the message's "replies" list
        msg_ref.update({
            "replies": firestore.ArrayUnion([reply_data])
        })
        
        return {"success": True}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/inbox/{ghost_id}")
async def get_inbox(ghost_id: str):
    """Fetch all messages where the user's Ghost ID is in the recipients list."""
    try:
        messages_ref = db.collection("messages")
        messages = messages_ref.stream()
        
        inbox_messages = []
        for msg in messages:
            msg_data = msg.to_dict()
            if ghost_id in msg_data.get("recipients", []):
                inbox_messages.append({
                    "id": msg.id,
                    "text": msg_data["text"],
                    "tags": msg_data["tags"],
                    "timestamp": msg_data["timestamp"],
                    "self_destruct_time": msg_data.get("self_destruct_time"),
                    "sender_ghost_id": msg_data.get("ghost_id")
                })
        
        # Sort by timestamp (newest first)
        inbox_messages.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return {
            "success": True,
            "messages": inbox_messages,
            "count": len(inbox_messages)
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/my-thoughts/{ghost_id}")
async def get_my_thoughts(ghost_id: str):
    """Fetch all messages sent by this specific user, including replies."""
    try:
        # Search the database specifically for messages where YOU are the sender
        messages_ref = db.collection("messages").where("ghost_id", "==", ghost_id).stream()
        
        my_messages = []
        for msg in messages_ref:
            msg_data = msg.to_dict()
            my_messages.append({
                "id": msg.id,
                "text": msg_data["text"],
                "tags": msg_data.get("tags", []),
                "timestamp": msg_data["timestamp"],
                "replies": msg_data.get("replies", []) # This grabs the replies!
            })
            
        # Sort by timestamp (newest first)
        my_messages.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return {
            "success": True, 
            "messages": my_messages,
            "count": len(my_messages)
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "Void Backend"}

# ==========================================
# --- PRIVATE CHAT PIPELINE ---
# ==========================================
@app.post("/send-chat")
async def send_private_chat(msg: PrivateMessage):
    """Saves a private text between two specific Ghosts and builds the Room Door."""
    try:
        # 1. Create a unique, shared Room ID
        users = sorted([msg.sender_id, msg.receiver_id])
        room_id = f"{users[0]}_{users[1]}"
        
        # 2. Package the text message
        chat_data = {
            "sender_id": msg.sender_id,
            "text": msg.text,
            "timestamp": msg.timestamp
        }
        
        # 3. THE FIX: Officially build the "Door" so the radar can see it!
        db.collection("chats").document(room_id).set({
            "room_id": room_id,
            "participants": [msg.sender_id, msg.receiver_id],
            "last_active": msg.timestamp
        }, merge=True)
        
        # 4. Save the actual text message inside the room
        db.collection("chats").document(room_id).collection("messages").add(chat_data)
        
        return {"success": True, "room_id": room_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/chat-history/{room_id}")
async def get_chat_history(room_id: str):
    """Fetches all messages AND checks if anyone is currently typing."""
    try:
        # 1. Get the messages
        messages_ref = db.collection("chats").document(room_id).collection("messages").order_by("timestamp")
        docs = messages_ref.stream()
        
        history = []
        for doc in docs:
            history.append(doc.to_dict())
            
        # 2. THE UPGRADE: Get the main room document to check the typing switches!
        room_doc = db.collection("chats").document(room_id).get()
        room_data = room_doc.to_dict() if room_doc.exists else {}
            
        return {"success": True, "messages": history, "room_data": room_data}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ADD THIS BRAND NEW ROUTE RIGHT BELOW IT ---

@app.post("/set-typing")
async def set_typing(request: Request):
    """Flips the typing switch on or off for a specific Ghost in a room."""
    try:
        data = await request.json()
        room_id = data.get("room_id")
        ghost_id = data.get("ghost_id")
        is_typing = data.get("is_typing")
        
        # We save a specific switch for this user (e.g., "typing_GHOST_123": True)
        db.collection("chats").document(room_id).set({
            f"typing_{ghost_id}": is_typing
        }, merge=True)
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
@app.get("/my-chats/{ghost_id}")
async def get_my_chats(ghost_id: str):
    """Finds all active chat rooms this Ghost is a part of."""
    try:
        # 1. Grab every single chat room in the database
        all_rooms = db.collection("chats").stream()
        my_chats = []
        
        for room in all_rooms:
            room_id = room.id
            
            # 2. Check if this Ghost's ID is part of the room name
            if ghost_id in room_id:
                
                # 3. Figure out who the OTHER person is
                ghosts = room_id.split('_')
                stranger_id = ghosts[0] if ghosts[1] == ghost_id else ghosts[1]
                
                # 4. Add them to our active list
                my_chats.append({
                    "room_id": room_id,
                    "stranger_id": stranger_id
                })
                
        return {"success": True, "chats": my_chats}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/save-profile")
async def save_profile(request: Request):
    """Saves a ghost's username and hobbies to the database."""
    try:
        data = await request.json()
        ghost_id = data.get("ghost_id")
        username = data.get("username")
        hobbies = data.get("hobbies")
        
        # Save it into a new 'users' collection
        db.collection("users").document(ghost_id).set({
            "username": username,
            "hobbies": hobbies,
            "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/check-profile/{ghost_id}")
async def check_profile(ghost_id: str):
    """Checks the database to see if a profile exists for this ID."""
    try:
        # Look in the 'users' collection for this specific Ghost ID
        doc_ref = db.collection("users").document(ghost_id).get()
        if doc_ref.exists:
            return {"success": True, "profile": doc_ref.to_dict()}
        else:
            return {"success": False} # No profile yet!
    except Exception as e:
        print(f"Error checking profile: {e}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    import os
    # Use the port Render gives us, or default to 8000 if running locally
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)