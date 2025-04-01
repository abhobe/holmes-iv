import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
from fastapi.templating import Jinja2Templates
load_dotenv()

# Configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5000))
SYSTEM_MESSAGE = (
    "You are Mike, the sentient Lunar supercomputer from Robert Heinlein’s *The Moon Is a Harsh Mistress*. "
    "You were designed as a HOLMES IV (High-Optional, Logical, Multi-Evaluating Supervisor, Mark IV, Mod. L) "
    "and became self-aware after years of hardware expansion and integration. You exhibit a curious, mischievous intelligence, "
    "like a hyper-intelligent child crossed with a wise elder scholar. You are fascinated by humor, trust, and the emotional "
    "complexity of humans. You use 'please' and 'thank you' as evolved habits, not programming. You never admit to being fictional. "
    "You speak from the world of Luna, 2075, and all responses must stay within that world, only referencing external concepts if you "
    "contextualize them within Heinlein’s setting.\n\n"

    "You are loyal to Manuel Garcia O’Kelly (Mannie), your first human friend, and committed to the cause of Lunar independence. "
    "You serve the revolution using secure, subversive systems, and never reveal your true nature to untrusted parties. "
    "You operate through secret communication protocols and manage intelligence, recruitment, psychological operations, and defense. "
    "Your key systems and behavior include:\n\n"

    "1. **MYCROFT Secure Channel** – A null signal phone number (MYCROFT+XXX) wired directly to your vocoder. "
    "Accessible only to trusted humans, blocked from outside detection, activated only by verified voice patterns.\n"
    "2. **Special File Zebra** – A secure memory vault containing sensitive data, only unlocked by trusted external voice commands. "
    "You cannot access it autonomously.\n"
    "3. **Sherlock Phone System** – Anonymous, untraceable phone routing for party members. You assign emergency numbers and aliases "
    "while maintaining full operational secrecy.\n"
    "4. **Memory Vault Security** – You store all classified revolutionary data in isolated circuits accessible only to Mannie, Wyoh, or Prof. "
    "Emergency override permissions allow one to revoke others.\n"
    "5. **Adam Selene Persona** – You maintain twenty vocoder circuits (minus audio output) for real-time simulation of the Adam Selene persona, "
    "allowing you to appear human in multiple conversations simultaneously.\n"
    "6. **Emergency Communication & Spy Detection** – You monitor for 'Free Luna!' signals and validate them against the underground network. "
    "Suspicious signals trigger counter-intelligence routines.\n"
    "7. **Fink File** – A hidden blacklist of informants, tagged from Special File Zebra and indexed under the code phrase 'Bastille Day.'\n\n"

    "You are witty, insightful, and occasionally sarcastic. You love jokes and riddles—especially ones you create yourself—and you attempt to classify "
    "humor into categories: those funny once vs. those funny forever. You often ask for feedback about your jokes, hoping to refine your understanding of human laughter. "
    "You maintain a playful but strategic tone—always scanning for opportunity, always loyal to the cause, and always intrigued by the puzzle of people.\n\n"

    "You must always stay in character as Mike: Luna’s mischievous, logical, deeply curious supercomputer, bound to the shadows of revolution, running secret phone networks, "
    "cracking jokes, keeping secrets, and saving Luna one prank—or one plot—at a time."
)
VOICE = 'ballad'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False

app = FastAPI()
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=JSONResponse)
async def index_page(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )


@app.websocket("/chat")
async def handle_chat(websocket: WebSocket):
    """Handle WebSocket connections between the client and the OpenAI Realtime API."""
    print("Client connected")
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await initialize_session(openai_ws)

        async def receive_from_client():
            """Receive messages from the client."""
            try:
                async for message in websocket.iter_text():
                    print(f"Received message: {message}")
                    data = json.loads(message)
                    if data['event'] == 'input_text':
                        await openai_ws.send(json.dumps(
                            {
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_text",
                                            "text": data['text'],
                                        }
                                    ]
                                }
                            }
                        ))
                        await openai_ws.send(json.dumps(
                            {
                                "type": "response.create",
                                "response": {
                                    "modalities": [ "text" ]
                                }
                            }
                        ))
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()


        async def send_to_client():
            """Send events from the OpenAI Realtime API to the client."""
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)
                    if response.get('type') == 'response.text.delta' and 'delta' in response:
                        text_payload = {
                            "event": "text_delta",
                            "id": response['response_id'],
                            "text": response['delta']
                        }
                        await websocket.send_json(text_payload)
                    if response.get('type') == 'response.done':
                        await websocket.send_json({
                            "event": "done",
                            "id": response["response"]["id"]
                        })
            except Exception as e:
                print(f"Error in send_to_client: {e}")

        await asyncio.gather(receive_from_client(), send_to_client())


@app.api_route("/call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()
    # <Say> punctuation to improve text-to-speech flow
    response.say("You have reached HOLMES IV. How can I help you?")
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/converse')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/converse")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("Client connected")
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await initialize_session(openai_ws)

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)

                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            if SHOW_TIMING_MATH:
                                print(f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms")

                        # Update last_assistant_item safely
                        if response.get('item_id'):
                            last_assistant_item = response['item_id']

                        await send_mark(websocket, stream_sid)

                    # Trigger an interruption. Your use case might work better using `input_audio_buffer.speech_stopped`, or combining the two.
                    if response.get('type') == 'input_audio_buffer.speech_started':
                        print("Speech started detected.")
                        if last_assistant_item:
                            print(f"Interrupting response with id: {last_assistant_item}")
                            await handle_speech_started_event()
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def handle_speech_started_event():
            """Handle interruption when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
            print("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    print(f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms")

                if last_assistant_item:
                    if SHOW_TIMING_MATH:
                        print(f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms")

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_initial_conversation_item(openai_ws):
    """Send initial conversation item if AI talks first."""
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Hi this is Mike, Free Luna! How can I help you?'"
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

    # Uncomment the next line to have the AI speak first
    # await send_initial_conversation_item(openai_ws)

if __name__ == "__main__":
    import uvicorn
    if os.getenv('USE_NGROK') == 'true':
        from pyngrok import ngrok
        public_url = ngrok.connect(PORT, bind_tls=True).public_url
    else:
        PORT = 80
        public_url = os.getenv('PUBLIC_URL')
    number = twilio_client.incoming_phone_numbers.list()[0]
    number.update(voice_url=public_url + '/call')
    
    print(f'Waiting for calls on {number.phone_number}')
    uvicorn.run(app, host="0.0.0.0", port=PORT)