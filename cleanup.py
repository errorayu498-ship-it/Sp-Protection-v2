"""
Optional Utilities for Bot Maintenance
Run: python cleanup.py
"""

import json
import os
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BotMaintenance:
    def __init__(self):
        self.db_file = 'bot_database.json'
        self.warnings_file = 'warnings.json'
    
    def cleanup_old_warnings(self, days=7):
        """Remove warnings older than specified days"""
        try:
            if not os.path.exists(self.warnings_file):
                logger.info("No warnings file found")
                return
            
            with open(self.warnings_file, 'r', encoding='utf-8') as f:
                warnings = json.load(f)
            
            cutoff_date = datetime.now() - timedelta(days=days)
            cleaned = 0
            
            for user_key in list(warnings.keys()):
                original_count = len(warnings[user_key])
                filtered = []
                
                for warning in warnings[user_key]:
                    warning_time = datetime.fromisoformat(warning['timestamp'])
                    if warning_time > cutoff_date:
                        filtered.append(warning)
                
                cleaned += (original_count - len(filtered))
                warnings[user_key] = filtered
            
            with open(self.warnings_file, 'w', encoding='utf-8') as f:
                json.dump(warnings, f, indent=4, ensure_ascii=False)
            
            logger.info(f"✅ Cleaned {cleaned} old warnings (older than {days} days)")
            return cleaned
        
        except Exception as e:
            logger.error(f"Cleanup Error: {e}")
            return 0
    
    def backup_database(self):
        """Create backup of both database files"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Backup database
            if os.path.exists(self.db_file):
                backup_db = f"backup_database_{timestamp}.json"
                with open(self.db_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                with open(backup_db, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                logger.info(f"✅ Database backed up: {backup_db}")
            
            # Backup warnings
            if os.path.exists(self.warnings_file):
                backup_warnings = f"backup_warnings_{timestamp}.json"
                with open(self.warnings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                with open(backup_warnings, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                logger.info(f"✅ Warnings backed up: {backup_warnings}")
        
        except Exception as e:
            logger.error(f"Backup Error: {e}")
    
    def get_statistics(self):
        """Get bot statistics"""
        try:
            stats = {
                'timestamp': datetime.now().isoformat(),
                'database': {},
                'warnings': {}
            }
            
            # Database stats
            if os.path.exists(self.db_file):
                with open(self.db_file, 'r', encoding='utf-8') as f:
                    db = json.load(f)
                stats['database'] = {
                    'allowed_links': len(db.get('allowed_links', [])),
                    'blocked_links': len(db.get('blocked_links', [])),
                    'spam_patterns': len(db.get('spam_patterns', []))
                }
            
            # Warnings stats
            if os.path.exists(self.warnings_file):
                with open(self.warnings_file, 'r', encoding='utf-8') as f:
                    warnings = json.load(f)
                
                total_warnings = sum(len(v) for v in warnings.values())
                warned_users = len(warnings)
                
                stats['warnings'] = {
                    'total_warnings': total_warnings,
                    'warned_users': warned_users,
                    'average_per_user': round(total_warnings / warned_users, 2) if warned_users > 0 else 0
                }
            
            return stats
        
        except Exception as e:
            logger.error(f"Statistics Error: {e}")
            return None
    
    def display_statistics(self):
        """Display statistics in readable format"""
        stats = self.get_statistics()
        if not stats:
            return
        
        print("\n" + "="*50)
        print("📊 BOT STATISTICS")
        print("="*50)
        print(f"Time: {stats['timestamp']}")
        print("\n📁 Database:")
        for key, value in stats['database'].items():
            print(f"  • {key}: {value}")
        
        print("\n⚠️ Warnings:")
        for key, value in stats['warnings'].items():
            print(f"  • {key}: {value}")
        print("="*50 + "\n")
    
    def remove_duplicate_links(self):
        """Remove duplicate links from database"""
        try:
            if not os.path.exists(self.db_file):
                logger.info("No database file found")
                return 0
            
            with open(self.db_file, 'r', encoding='utf-8') as f:
                db = json.load(f)
            
            # Remove duplicates
            duplicates = 0
            for list_type in ['allowed_links', 'blocked_links', 'spam_patterns']:
                original_count = len(db.get(list_type, []))
                db[list_type] = list(set(db.get(list_type, [])))
                duplicates += (original_count - len(db[list_type]))
            
            with open(self.db_file, 'w', encoding='utf-8') as f:
                json.dump(db, f, indent=4, ensure_ascii=False)
            
            logger.info(f"✅ Removed {duplicates} duplicate entries")
            return duplicates
        
        except Exception as e:
            logger.error(f"Dedup Error: {e}")
            return 0
    
    def export_warnings_csv(self):
        """Export warnings to CSV format"""
        try:
            if not os.path.exists(self.warnings_file):
                logger.info("No warnings file found")
                return
            
            with open(self.warnings_file, 'r', encoding='utf-8') as f:
                warnings = json.load(f)
            
            csv_file = f"warnings_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            with open(csv_file, 'w', encoding='utf-8') as f:
                f.write("User_ID,Guild_ID,Timestamp,Reason,Warning_Count\n")
                
                for user_key, user_warnings in warnings.items():
                    parts = user_key.split('_')
                    guild_id = parts[0] if len(parts) > 1 else "Unknown"
                    user_id = parts[1] if len(parts) > 1 else parts[0]
                    
                    for idx, warning in enumerate(user_warnings, 1):
                        f.write(f'{user_id},{guild_id},"{warning["timestamp"]}","{warning["reason"]}",{idx}\n')
            
            logger.info(f"✅ Warnings exported to: {csv_file}")
        
        except Exception as e:
            logger.error(f"Export Error: {e}")
    
    def run_full_maintenance(self):
        """Run all maintenance tasks"""
        print("\n🔧 Starting Full Maintenance...\n")
        
        # Backup
        print("1️⃣ Creating backups...")
        self.backup_database()
        
        # Clean duplicates
        print("2️⃣ Removing duplicates...")
        self.remove_duplicate_links()
        
        # Clean old warnings
        print("3️⃣ Cleaning old warnings...")
        self.cleanup_old_warnings(days=7)
        
        # Show statistics
        print("4️⃣ Generating statistics...\n")
        self.display_statistics()
        
        # Export warnings
        print("5️⃣ Exporting warnings...")
        self.export_warnings_csv()
        
        print("✅ Maintenance completed!\n")

# ==================== MENU ====================
def main():
    print("\n" + "="*50)
    print("🤖 BOT MAINTENANCE UTILITY")
    print("="*50)
    
    maintenance = BotMaintenance()
    
    while True:
        print("\nChoose an option:")
        print("1. Show Statistics")
        print("2. Cleanup Old Warnings")
        print("3. Backup Database")
        print("4. Remove Duplicate Links")
        print("5. Export Warnings (CSV)")
        print("6. Run Full Maintenance")
        print("7. Exit")
        
        choice = input("\nEnter choice (1-7): ").strip()
        
        if choice == '1':
            maintenance.display_statistics()
        elif choice == '2':
            days = input("Enter days threshold (default 7): ").strip()
            days = int(days) if days.isdigit() else 7
            maintenance.cleanup_old_warnings(days)
        elif choice == '3':
            maintenance.backup_database()
        elif choice == '4':
            maintenance.remove_duplicate_links()
        elif choice == '5':
            maintenance.export_warnings_csv()
        elif choice == '6':
            maintenance.run_full_maintenance()
        elif choice == '7':
            print("Goodbye! 👋")
            break
        else:
            print("❌ Invalid choice")

if __name__ == "__main__":
    main()
