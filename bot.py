import sys
import os

# Fix for Python 3.13+ audioop issue
if sys.version_info >= (3, 13):
    import unittest.mock as mock
    sys.modules['audioop'] = mock.MagicMock()

import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import aiohttp
import logging
from dotenv import load_dotenv
import re
import asyncio

# Logger Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load Environment Variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
MOD_LOG_CHANNEL_ID = int(os.getenv('MOD_LOG_CHANNEL_ID', '0'))

# Bot Configuration
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix='/', intents=intents)

# Database File
DATABASE_FILE = 'bot_database.json'
WARNINGS_FILE = 'warnings.json'

# ==================== DATABASE MANAGEMENT ====================
class DatabaseManager:
    def __init__(self):
        self.db_file = DATABASE_FILE
        self.warnings_file = WARNINGS_FILE
        self.load_database()
    
    def load_database(self):
        """Load or create database"""
        if not os.path.exists(self.db_file):
            self.data = {
                'servers': {},
                'blocked_links': [],
                'allowed_links': ['youtube.com', 'youtu.be', 'tiktok.com', 'facebook.com', 
                                 'instagram.com', 'kick.com', 'twitch.tv', 'groq.ai'],
                'spam_patterns': []
            }
            self.save_database()
        else:
            with open(self.db_file, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
        
        if not os.path.exists(self.warnings_file):
            self.warnings = {}
            self.save_warnings()
        else:
            with open(self.warnings_file, 'r', encoding='utf-8') as f:
                self.warnings = json.load(f)
    
    def save_database(self):
        """Save database to file"""
        try:
            with open(self.db_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Database Save Error: {e}")
    
    def save_warnings(self):
        """Save warnings to file"""
        try:
            with open(self.warnings_file, 'w', encoding='utf-8') as f:
                json.dump(self.warnings, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Warnings Save Error: {e}")

db = DatabaseManager()

# ==================== GROQ AI LINK DETECTION ====================
class GroqAIDetector:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"
    
    async def analyze_link(self, url: str) -> Optional[Dict]:
        """Analyze link using Groq AI"""
        try:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            
            prompt = f"""Analyze this link and determine if it's safe:
URL: {url}

Respond ONLY in JSON:
{{"is_safe": true/false, "category": "social_media|adult|spam|malware|discord_invite|grabber|nuker|unknown", "confidence": 0.0-1.0, "reason": "brief"}}"""
            
            payload = {
                'model': 'mixtral-8x7b-32768',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 150,
                'temperature': 0.3
            }
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.endpoint, 
                        json=payload, 
                        headers=headers, 
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            content = data['choices'][0]['message']['content']
                            result = json.loads(content)
                            return result
                        else:
                            logger.warning(f"Groq API Status: {resp.status}")
                            return None
            except asyncio.TimeoutError:
                logger.warning("Groq API Timeout - using fallback")
                return None
            except Exception as e:
                logger.warning(f"Groq Connection Error: {e}")
                return None
        except Exception as e:
            logger.error(f"Groq AI Error: {e}")
            return None

# Initialize Groq AI
try:
    groq_ai = GroqAIDetector(GROQ_API_KEY) if GROQ_API_KEY else None
    if groq_ai:
        logger.info("✅ Groq AI initialized")
except:
    groq_ai = None
    logger.warning("Groq AI not initialized")

# ==================== LINK DETECTION ====================
class LinkDetector:
    def __init__(self):
        self.url_pattern = re.compile(
            r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
        )
    
    def extract_urls(self, text: str) -> List[str]:
        """Extract URLs from text"""
        return self.url_pattern.findall(text)
    
    def get_domain(self, url: str) -> str:
        """Extract domain from URL"""
        try:
            domain = url.replace('https://', '').replace('http://', '').split('/')[0].split('?')[0]
            return domain.lower()
        except:
            return url.lower()
    
    async def is_allowed(self, url: str) -> bool:
        """Check if URL is allowed"""
        domain = self.get_domain(url)
        
        # Check allowed list first
        for allowed in db.data['allowed_links']:
            if allowed.lower() in domain:
                logger.info(f"✅ Allowed: {domain}")
                return True
        
        # Check blocked list
        for blocked in db.data['blocked_links']:
            if blocked.lower() in domain:
                logger.info(f"❌ Blocked: {domain}")
                return False
        
        # Use Groq AI if available
        if groq_ai:
            analysis = await groq_ai.analyze_link(url)
            if analysis:
                is_safe = analysis.get('is_safe', False)
                logger.info(f"AI Analysis: {domain} - Safe: {is_safe}")
                return is_safe
        
        # Default: block unknown
        logger.info(f"⚠️ Unknown: {domain} - Blocking by default")
        return False

detector = LinkDetector()

# ==================== WARNING SYSTEM ====================
class WarningSystem:
    def __init__(self):
        self.timeout_durations = {
            1: timedelta(hours=1),
            2: timedelta(days=1),
            3: timedelta(days=7)
        }
    
    async def add_warning(self, guild_id: str, user_id: str, reason: str) -> int:
        """Add warning to user"""
        key = f"{guild_id}_{user_id}"
        
        if key not in db.warnings:
            db.warnings[key] = []
        
        warning_entry = {
            'timestamp': datetime.now().isoformat(),
            'reason': reason
        }
        
        db.warnings[key].append(warning_entry)
        db.save_warnings()
        
        return len(db.warnings[key])
    
    def get_warnings(self, guild_id: str, user_id: str) -> int:
        """Get warning count"""
        key = f"{guild_id}_{user_id}"
        return len(db.warnings.get(key, []))
    
    async def clear_warnings(self, guild_id: str, user_id: str):
        """Clear warnings"""
        key = f"{guild_id}_{user_id}"
        if key in db.warnings:
            db.warnings[key] = []
            db.save_warnings()

warning_system = WarningSystem()

# ==================== BOT EVENTS ====================
@bot.event
async def on_ready():
    """Bot startup"""
    try:
        await bot.tree.sync()
        logger.info(f"✅ Bot logged in as {bot.user}")
        logger.info(f"🔧 Groq AI: {'✅ Active' if groq_ai else '❌ Inactive'}")
        cleanup_old_warnings.start()
        logger.info("✅ Cleanup task started")
    except Exception as e:
        logger.error(f"Startup Error: {e}")

@bot.event
async def on_message(message: discord.Message):
    """Message handler"""
    try:
        if message.author.bot:
            return
        
        if not message.guild:
            return
        
        urls = detector.extract_urls(message.content)
        
        if urls:
            blocked_urls = []
            for url in urls:
                is_safe = await detector.is_allowed(url)
                if not is_safe:
                    blocked_urls.append(url)
            
            if blocked_urls:
                # Add warning
                warning_count = await warning_system.add_warning(
                    str(message.guild.id),
                    str(message.author.id),
                    f"Blocked Link: {', '.join(blocked_urls)}"
                )
                
                logger.info(f"⚠️ Warning {warning_count}/3 for {message.author}")
                
                # Delete message
                try:
                    await message.delete()
                except:
                    pass
                
                # Send DM
                try:
                    embed = discord.Embed(
                        title="⚠️ Warning",
                        description=f"**Server:** {message.guild.name}\n**Links:** {', '.join(blocked_urls)}\n\n**Warning:** {warning_count}/3",
                        color=discord.Color.red() if warning_count < 3 else discord.Color.dark_red()
                    )
                    embed.set_footer(text="3 warnings = 7 day timeout")
                    await message.author.send(embed=embed)
                except:
                    pass
                
                # Log
                await log_moderation(message.guild, message.author, "Blocked Link", 
                                   f"Links: {', '.join(blocked_urls)}", warning_count)
                
                # Timeout
                if warning_count >= 3:
                    try:
                        await message.author.timeout(timedelta(days=7), reason="3 warnings")
                        await log_moderation(message.guild, message.author, "Timeout", "7 days", 3)
                    except Exception as e:
                        logger.error(f"Timeout Error: {e}")
                elif warning_count in [1, 2]:
                    try:
                        timeout_duration = warning_system.timeout_durations[warning_count]
                        await message.author.timeout(timeout_duration, reason=f"Warning {warning_count}")
                    except:
                        pass
                
                return
        
        await bot.process_commands(message)
    
    except Exception as e:
        logger.error(f"Message Error: {e}")

# ==================== MODERATION LOGGING ====================
async def log_moderation(guild: discord.Guild, user: discord.User, action: str, details: str, warning_count: int = 0):
    """Log moderation action"""
    try:
        if MOD_LOG_CHANNEL_ID == 0:
            return
        
        channel = guild.get_channel(MOD_LOG_CHANNEL_ID)
        if not channel:
            return
        
        embed = discord.Embed(
            title=f"🔨 {action}",
            description=f"**User:** {user.mention}\n**Details:** {details}",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        
        if warning_count > 0:
            embed.add_field(name="Warnings", value=f"{warning_count}/3", inline=False)
        
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Log Error: {e}")

# ==================== COMMANDS ====================
@bot.tree.command(name="allowed_links", description="Add allowed link")
@discord.app_commands.describe(link="Link to allow")
async def allowed_links(interaction: discord.Interaction, link: str):
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        domain = detector.get_domain(link)
        
        if domain in db.data['allowed_links']:
            await interaction.response.send_message(f"✅ Already allowed: `{domain}`", ephemeral=True)
            return
        
        db.data['allowed_links'].append(domain)
        db.save_database()
        
        embed = discord.Embed(
            title="✅ Link Added",
            description=f"**Domain:** `{domain}`",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await log_moderation(interaction.guild, interaction.user, "Link Allowed", f"Domain: {domain}")
    except Exception as e:
        logger.error(f"Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)[:100]}", ephemeral=True)

@bot.tree.command(name="block_link", description="Block a link")
@discord.app_commands.describe(link="Link to block")
async def block_link(interaction: discord.Interaction, link: str):
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        domain = detector.get_domain(link)
        
        if domain in db.data['blocked_links']:
            await interaction.response.send_message(f"✅ Already blocked: `{domain}`", ephemeral=True)
            return
        
        db.data['blocked_links'].append(domain)
        db.save_database()
        
        embed = discord.Embed(
            title="🚫 Link Blocked",
            description=f"**Domain:** `{domain}`",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await log_moderation(interaction.guild, interaction.user, "Link Blocked", f"Domain: {domain}")
    except Exception as e:
        logger.error(f"Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)[:100]}", ephemeral=True)

@bot.tree.command(name="show_info", description="Show bot status")
async def show_info(interaction: discord.Interaction):
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🤖 Bot Status",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name="Status",
            value=f"**Online:** ✅\n**Groq AI:** {'✅' if groq_ai else '❌'}\n**Database:** ✅",
            inline=False
        )
        
        allowed_text = "\n".join(f"{i+1}. `{link}`" for i, link in enumerate(db.data['allowed_links'][:5]))
        embed.add_field(name=f"✅ Allowed ({len(db.data['allowed_links'])})", value=allowed_text, inline=False)
        
        blocked_text = "\n".join(f"{i+1}. `{link}`" for i, link in enumerate(db.data['blocked_links'][:5]))
        embed.add_field(name=f"❌ Blocked ({len(db.data['blocked_links'])})", value=blocked_text or "None", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)[:100]}", ephemeral=True)

@bot.tree.command(name="remove_link", description="Remove link by ID")
@discord.app_commands.describe(link_id="ID number", link_type="allowed or blocked")
async def remove_link(interaction: discord.Interaction, link_id: int, link_type: str):
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        if link_type.lower() not in ['allowed', 'blocked']:
            await interaction.response.send_message("❌ Type: 'allowed' or 'blocked'", ephemeral=True)
            return
        
        target = db.data['allowed_links'] if link_type.lower() == 'allowed' else db.data['blocked_links']
        
        if link_id < 1 or link_id > len(target):
            await interaction.response.send_message(f"❌ Invalid ID (1-{len(target)})", ephemeral=True)
            return
        
        removed = target[link_id - 1]
        target.pop(link_id - 1)
        db.save_database()
        
        embed = discord.Embed(
            title="✅ Removed",
            description=f"**Link:** `{removed}`",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await log_moderation(interaction.guild, interaction.user, "Link Removed", f"Link: {removed}")
    except Exception as e:
        logger.error(f"Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)[:100]}", ephemeral=True)

@bot.tree.command(name="warnings", description="Check user warnings")
@discord.app_commands.describe(user="User to check")
async def warnings(interaction: discord.Interaction, user: discord.User):
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        count = warning_system.get_warnings(str(interaction.guild.id), str(user.id))
        
        embed = discord.Embed(
            title=f"⚠️ {user.name}",
            description=f"Warnings: {count}/3",
            color=discord.Color.green() if count == 0 else (discord.Color.orange() if count < 3 else discord.Color.red())
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)[:100]}", ephemeral=True)

@bot.tree.command(name="clear_warnings", description="Clear user warnings")
@discord.app_commands.describe(user="User")
async def clear_warnings(interaction: discord.Interaction, user: discord.User):
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        await warning_system.clear_warnings(str(interaction.guild.id), str(user.id))
        
        embed = discord.Embed(
            title="✅ Cleared",
            description=f"Warnings cleared for {user.mention}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await log_moderation(interaction.guild, interaction.user, "Warnings Cleared", f"User: {user.mention}")
    except Exception as e:
        logger.error(f"Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)[:100]}", ephemeral=True)

@bot.tree.command(name="cmds", description="Show commands")
async def cmds(interaction: discord.Interaction):
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        embed = discord.Embed(title="📋 Commands", color=discord.Color.blue())
        embed.add_field(name="/allowed_links <link>", value="Allow link", inline=False)
        embed.add_field(name="/block_link <link>", value="Block link", inline=False)
        embed.add_field(name="/remove_link <id> <type>", value="Remove by ID", inline=False)
        embed.add_field(name="/show_info", value="Show status", inline=False)
        embed.add_field(name="/warnings <@user>", value="Check warnings", inline=False)
        embed.add_field(name="/clear_warnings <@user>", value="Clear warnings", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)[:100]}", ephemeral=True)

# ==================== CLEANUP ====================
@tasks.loop(hours=24)
async def cleanup_old_warnings():
    try:
        cutoff = datetime.now() - timedelta(days=7)
        cleaned = 0
        
        for key in list(db.warnings.keys()):
            filtered = []
            for w in db.warnings[key]:
                try:
                    if datetime.fromisoformat(w['timestamp']) > cutoff:
                        filtered.append(w)
                    else:
                        cleaned += 1
                except:
                    pass
            
            if not filtered:
                del db.warnings[key]
            else:
                db.warnings[key] = filtered
        
        db.save_warnings()
        logger.info(f"✅ Cleanup: {cleaned} old warnings removed")
    except Exception as e:
        logger.error(f"Cleanup Error: {e}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    logger.error(f"Command Error: {error}")
    try:
        await interaction.response.send_message(f"❌ Error: {str(error)[:100]}", ephemeral=True)
    except:
        pass

# ==================== START ====================
if __name__ == "__main__":
    try:
        logger.info("🚀 Starting bot...")
        if not DISCORD_TOKEN:
            logger.critical("❌ DISCORD_TOKEN missing in .env")
        else:
            bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.critical(f"❌ Failed: {e}")
