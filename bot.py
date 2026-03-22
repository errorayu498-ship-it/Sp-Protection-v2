import discord
from discord.ext import commands, tasks
import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import aiohttp
import logging
from dotenv import load_dotenv
from collections import defaultdict
import re

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
            self.warnings = defaultdict(lambda: defaultdict(list))
            self.save_warnings()
        else:
            with open(self.warnings_file, 'r', encoding='utf-8') as f:
                self.warnings = defaultdict(lambda: defaultdict(list), 
                                          {k: defaultdict(list, v) for k, v in json.load(f).items()})
    
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
            warnings_dict = {k: dict(v) for k, v in self.warnings.items()}
            with open(self.warnings_file, 'w', encoding='utf-8') as f:
                json.dump(warnings_dict, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Warnings Save Error: {e}")

db = DatabaseManager()

# ==================== GROQ AI LINK DETECTION ====================
class GroqAIDetector:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"
    
    async def analyze_link(self, url: str) -> Dict:
        """Analyze link using Groq AI"""
        try:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            
            prompt = f"""Analyze this link and determine if it's safe or spam:
URL: {url}

Respond ONLY in JSON format:
{{
    "is_safe": true/false,
    "category": "social_media|adult|spam|malware|discord_invite|grabber|nuker|unknown",
    "confidence": 0.0-1.0,
    "reason": "brief reason"
}}"""
            
            payload = {
                'model': 'mixtral-8x7b-32768',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 200,
                'temperature': 0.3
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.endpoint, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data['choices'][0]['message']['content']
                        # Parse JSON from response
                        try:
                            import json as js
                            result = js.loads(content)
                            return result
                        except:
                            return {'is_safe': False, 'category': 'unknown', 'confidence': 0.5, 'reason': 'Parse error'}
                    else:
                        logger.error(f"Groq API Error: {resp.status}")
                        return None
        except asyncio.TimeoutError:
            logger.warning("Groq API Timeout")
            return None
        except Exception as e:
            logger.error(f"Groq AI Error: {e}")
            return None

# Initialize Groq AI
try:
    groq_ai = GroqAIDetector(GROQ_API_KEY)
except:
    groq_ai = None
    logger.warning("Groq AI not initialized - using pattern matching only")

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
            domain = url.replace('https://', '').replace('http://', '').split('/')[0]
            return domain
        except:
            return url
    
    async def is_allowed(self, url: str) -> bool:
        """Check if URL is allowed"""
        domain = self.get_domain(url)
        
        # Check allowed list
        for allowed in db.data['allowed_links']:
            if allowed in domain:
                return True
        
        # Check blocked list
        for blocked in db.data['blocked_links']:
            if blocked in domain:
                return False
        
        # Use Groq AI if available
        if groq_ai:
            analysis = await groq_ai.analyze_link(url)
            if analysis and analysis.get('is_safe'):
                return True
        
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
        warning_entry = {
            'timestamp': datetime.now().isoformat(),
            'reason': reason
        }
        
        db.warnings[key].append(warning_entry)
        db.save_warnings()
        
        warning_count = len(db.warnings[key])
        return warning_count
    
    def get_warnings(self, guild_id: str, user_id: str) -> int:
        """Get warning count"""
        key = f"{guild_id}_{user_id}"
        return len(db.warnings[key])
    
    async def clear_warnings(self, guild_id: str, user_id: str):
        """Clear warnings"""
        key = f"{guild_id}_{user_id}"
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
        logger.info(f"🔧 Groq AI: {'Active' if groq_ai else 'Inactive'}")
        cleanup_old_warnings.start()
    except Exception as e:
        logger.error(f"Startup Error: {e}")

@bot.event
async def on_message(message: discord.Message):
    """Message handler"""
    try:
        # Ignore bot messages
        if message.author.bot:
            return
        
        # Check for links
        if message.guild:
            urls = detector.extract_urls(message.content)
            
            if urls:
                blocked_urls = []
                for url in urls:
                    if not await detector.is_allowed(url):
                        blocked_urls.append(url)
                
                if blocked_urls:
                    # Add warning
                    warning_count = await warning_system.add_warning(
                        str(message.guild.id),
                        str(message.author.id),
                        f"Spam/Blocked Link: {', '.join(blocked_urls)}"
                    )
                    
                    # Delete message
                    try:
                        await message.delete()
                    except:
                        pass
                    
                    # Send DM warning
                    try:
                        embed = discord.Embed(
                            title="⚠️ Warning",
                            description=f"**Server:** {message.guild.name}\n\n**Reason:** Blocked Link Detected\n**Links:** {', '.join(blocked_urls)}\n\n**Warning:** {warning_count}/3",
                            color=discord.Color.red() if warning_count < 3 else discord.Color.dark_red()
                        )
                        embed.set_footer(text=f"3 warnings = 1 week timeout")
                        await message.author.send(embed=embed)
                    except:
                        pass
                    
                    # Log to mod channel
                    await log_moderation(message.guild, message.author, "Blocked Link", f"Links: {', '.join(blocked_urls)}", warning_count)
                    
                    # Apply timeout if 3 warnings
                    if warning_count == 3:
                        try:
                            await message.author.timeout(timedelta(days=7), reason="3 warnings reached")
                            await log_moderation(message.guild, message.author, "Timeout Applied", "7 days timeout", 3)
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
        logger.error(f"Message Handler Error: {e}")

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
        logger.error(f"Logging Error: {e}")

# ==================== ADMIN COMMANDS ====================
@bot.tree.command(name="allowed_links", description="Add allowed link")
@discord.app_commands.describe(link="Link to allow")
async def allowed_links(interaction: discord.Interaction, link: str):
    """Add allowed link"""
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
            description=f"**Domain:** `{domain}`\n**Category:** Allowed",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await log_moderation(interaction.guild, interaction.user, "Link Allowed", f"Domain: {domain}", 0)
    
    except Exception as e:
        logger.error(f"Command Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

@bot.tree.command(name="block_link", description="Block a link")
@discord.app_commands.describe(link="Link to block")
async def block_link(interaction: discord.Interaction, link: str):
    """Block a link"""
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
            description=f"**Domain:** `{domain}`\n**Category:** Blocked",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await log_moderation(interaction.guild, interaction.user, "Link Blocked", f"Domain: {domain}", 0)
    
    except Exception as e:
        logger.error(f"Command Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

@bot.tree.command(name="show_info", description="Show bot status and links")
async def show_info(interaction: discord.Interaction):
    """Show bot info"""
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        # Create main embed
        main_embed = discord.Embed(
            title="🤖 Bot Status & Configuration",
            description="Advanced Discord Spam Protection Bot",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        main_embed.add_field(
            name="📊 Bot Status",
            value=f"**Status:** Online\n**Groq AI:** {'✅ Active' if groq_ai else '❌ Inactive'}\n**Database:** ✅ Active",
            inline=False
        )
        
        main_embed.add_field(
            name="🎯 Allowed Links",
            value=f"Total: {len(db.data['allowed_links'])}\n" + "\n".join(f"`{i+1}. {link}`" for i, link in enumerate(db.data['allowed_links'][:10])),
            inline=False
        )
        
        main_embed.add_field(
            name="🚫 Blocked Links",
            value=f"Total: {len(db.data['blocked_links'])}\n" + ("\n".join(f"`{i+1}. {link}`" for i, link in enumerate(db.data['blocked_links'][:10])) if db.data['blocked_links'] else "None"),
            inline=False
        )
        
        main_embed.set_footer(text="Bot by Admin | Made with ❤️")
        
        await interaction.response.send_message(embed=main_embed, ephemeral=True)
    
    except Exception as e:
        logger.error(f"Command Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

@bot.tree.command(name="remove_link", description="Remove allowed/blocked link by ID")
@discord.app_commands.describe(link_id="Link ID to remove", link_type="Type: allowed or blocked")
async def remove_link(interaction: discord.Interaction, link_id: int, link_type: str):
    """Remove link by ID"""
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        if link_type.lower() not in ['allowed', 'blocked']:
            await interaction.response.send_message("❌ Type must be 'allowed' or 'blocked'", ephemeral=True)
            return
        
        target_list = db.data['allowed_links'] if link_type.lower() == 'allowed' else db.data['blocked_links']
        
        if link_id < 1 or link_id > len(target_list):
            await interaction.response.send_message(f"❌ Invalid ID (1-{len(target_list)})", ephemeral=True)
            return
        
        removed_link = target_list[link_id - 1]
        target_list.pop(link_id - 1)
        db.save_database()
        
        embed = discord.Embed(
            title="✅ Link Removed",
            description=f"**Link:** `{removed_link}`\n**Category:** {link_type.title()}\n**ID:** {link_id}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await log_moderation(interaction.guild, interaction.user, "Link Removed", f"Domain: {removed_link} (ID: {link_id}, Type: {link_type})", 0)
    
    except Exception as e:
        logger.error(f"Command Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

@bot.tree.command(name="cmds", description="Show all admin commands")
async def cmds(interaction: discord.Interaction):
    """Show commands"""
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="📋 Admin Commands",
            color=discord.Color.blue()
        )
        
        commands_list = [
            ("/allowed_links <link>", "Add link to allowed list"),
            ("/block_link <link>", "Add link to blocked list"),
            ("/remove_link <id> <type>", "Remove link by ID (allowed/blocked)"),
            ("/show_info", "Show bot status & configuration"),
            ("/cmds", "Show all commands"),
            ("/warnings <@user>", "Show user warnings"),
            ("/clear_warnings <@user>", "Clear user warnings")
        ]
        
        for cmd, desc in commands_list:
            embed.add_field(name=cmd, value=desc, inline=False)
        
        embed.set_footer(text="⚠️ Admin only commands")
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    except Exception as e:
        logger.error(f"Command Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

@bot.tree.command(name="warnings", description="Check user warnings")
@discord.app_commands.describe(user="User to check")
async def warnings(interaction: discord.Interaction, user: discord.User):
    """Check user warnings"""
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        warning_count = warning_system.get_warnings(str(interaction.guild.id), str(user.id))
        
        embed = discord.Embed(
            title=f"⚠️ Warnings - {user.name}",
            description=f"**Total Warnings:** {warning_count}/3",
            color=discord.Color.orange()
        )
        
        if warning_count == 0:
            embed.description = "✅ No warnings"
        elif warning_count == 3:
            embed.color = discord.Color.dark_red()
            embed.description = f"**Total Warnings:** {warning_count}/3\n🔴 User will be timed out"
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    except Exception as e:
        logger.error(f"Command Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

@bot.tree.command(name="clear_warnings", description="Clear user warnings")
@discord.app_commands.describe(user="User to clear")
async def clear_warnings(interaction: discord.Interaction, user: discord.User):
    """Clear user warnings"""
    try:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only", ephemeral=True)
            return
        
        await warning_system.clear_warnings(str(interaction.guild.id), str(user.id))
        
        embed = discord.Embed(
            title="✅ Warnings Cleared",
            description=f"**User:** {user.mention}\n**Action:** All warnings removed",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await log_moderation(interaction.guild, interaction.user, "Warnings Cleared", f"User: {user.mention}", 0)
    
    except Exception as e:
        logger.error(f"Command Error: {e}")
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

# ==================== CLEANUP TASK ====================
@tasks.loop(hours=24)
async def cleanup_old_warnings():
    """Remove warnings older than 1 week"""
    try:
        one_week_ago = datetime.now() - timedelta(days=7)
        
        for key in list(db.warnings.keys()):
            warnings_list = db.warnings[key]
            filtered = []
            
            for w in warnings_list:
                warning_time = datetime.fromisoformat(w['timestamp'])
                if warning_time > one_week_ago:
                    filtered.append(w)
            
            db.warnings[key] = filtered
        
        db.save_warnings()
        logger.info("✅ Old warnings cleanup completed")
    except Exception as e:
        logger.error(f"Cleanup Error: {e}")

# ==================== ERROR HANDLER ====================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """Global error handler"""
    logger.error(f"Command Error: {error}")
    
    try:
        await interaction.response.send_message(
            f"❌ Error: {str(error)[:100]}",
            ephemeral=True
        )
    except:
        pass

# ==================== START BOT ====================
if __name__ == "__main__":
    try:
        logger.info("🚀 Starting bot...")
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}")
