# 🤖 AI Assistant Setup Guide

Your VBAMS project now has an AI-powered chat assistant integrated into the user dashboard!

## Features

✅ **Real-time Chat Support** - AI assistant available in the user dashboard
✅ **Smart Responses** - Uses Google Gemini for intelligent replies
✅ **Service Information** - Answers FAQs about breakdown assistance
✅ **Troubleshooting** - Helps with common vehicle issues
✅ **Minimizable Widget** - Chat panel can be minimized/expanded

## Setup Steps

### 1. Get Google Gemini API Key

1. Visit: **https://ai.google.dev/**
2. Click "Get API Key" or "Sign in"
3. Create a new project (or use existing one)
4. Generate an API key
5. Copy the key

### 2. Install Required Packages

Run the following command in your project directory:

```bash
pip install -r requirements.txt
```

Or manually install the new packages:

```bash
pip install google-generativeai python-dotenv
```

### 3. Configure Environment Variables

#### Option A: Using .env file (Recommended)

1. Copy `.env.example` to `.env`:
   ```bash
   copy .env.example .env
   ```

2. Open `.env` and add your API key:
   ```
   GEMINI_API_KEY=your_google_gemini_api_key_here
   SECRET_KEY=your_super_secret_session_key_here
   ```

#### Option B: System Environment Variable

If you prefer not to use a `.env` file, set the environment variable directly:

**Windows (PowerShell):**
```powershell
$env:GEMINI_API_KEY="your_google_gemini_api_key_here"
```

**Windows (Command Prompt):**
```cmd
set GEMINI_API_KEY=your_google_gemini_api_key_here
```

### 4. Restart Your Application

Once you've set the API key:

```bash
python main.py
```

or if using uvicorn:

```bash
uvicorn main:app --reload
```

### 5. Test the AI Assistant

1. Go to the **User Dashboard** (after logging in)
2. Look for the **🤖 AI Assistant** widget in the bottom-right corner
3. Click on it to expand the chat
4. Type a message and press Enter or click Send

## Example Queries You Can Ask

- "What services do you provide?"
- "How do I track my mechanic?"
- "My car won't start, what should I do?"
- "How long does it take to get help?"
- "What are your service hours?"
- "How do I register for the service?"
- "My car is making a strange noise"

## Troubleshooting

### Chat Not Working?

**Error: "AI service not configured"**
- Make sure your `GEMINI_API_KEY` is set correctly
- Check that the API key is from Google AI Studio (https://ai.google.dev/)
- Restart the application after setting the key

**Error: "Error processing your request"**
- Check your internet connection
- Verify the API key is valid
- Check the console for more detailed error messages

**Chat appears but AI doesn't respond?**
- Ensure you have internet access
- Check that the API key has quota remaining
- Review Google Cloud console for any usage limits

## Security Notes

⚠️ **Important:**
- **Never** commit your `.env` file to version control
- Add `.env` to your `.gitignore` file
- The `.env.example` file is safe to share (it shows the format only)
- Keep your API key private - treat it like a password

## How It Works

```
User types message
     ↓
Frontend sends to backend (/ai_chat endpoint)
     ↓
Backend sends to Google Gemini API
     ↓
Gemini generates intelligent response
     ↓
Response sent back to frontend
     ↓
Chat displays AI response
```

## Customization

To customize the AI assistant's behavior, edit the **system prompt** in `main.py` at the `/ai_chat` endpoint:

```python
system_prompt = """You are a helpful customer support assistant for BreakdownAssist...
```

You can modify this to change the personality, tone, or behavior of the AI.

## API Limits

Google Generative AI has rate limits:
- **Free tier:** 60 requests per minute
- **Free tier:** 1 request per second per API key

If you need higher limits, upgrade to a paid plan on Google Cloud.

## Support

For issues with:
- **Google Gemini API:** https://ai.google.dev/
- **FastAPI:** https://fastapi.tiangolo.com/
- **Your project:** Check the console logs and error messages

---

**Setup Complete! 🎉**

Your AI assistant is now ready to help users on your VBAMS platform!
