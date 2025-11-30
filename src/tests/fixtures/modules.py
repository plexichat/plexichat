"""
Module registry with lazy loading.

Provides lazy initialization of core modules to avoid
loading everything when only a subset is needed.
"""

from typing import Any, Dict, Optional


class ModuleRegistry:
    """
    Lazy-loading registry for core modules.
    
    Modules are only initialized when first accessed,
    and cached for subsequent accesses.
    
    Usage:
        modules = ModuleRegistry(db)
        auth = modules.auth  # Initializes auth module
        messaging = modules.messaging  # Initializes messaging (and auth if needed)
    """
    
    def __init__(self, db):
        self._db = db
        self._cache: Dict[str, Any] = {}
    
    def _reset_module(self, module):
        """Reset a module's internal state for fresh initialization."""
        module._manager = None
        module._setup_complete = False
    
    @property
    def auth(self):
        """Get the auth module (lazy loaded)."""
        if 'auth' not in self._cache:
            from src.core import auth
            self._reset_module(auth)
            auth.setup(self._db)
            self._cache['auth'] = auth
        return self._cache['auth']
    
    @property
    def messaging(self):
        """Get the messaging module (lazy loaded)."""
        if 'messaging' not in self._cache:
            from src.core import messaging
            self._reset_module(messaging)
            messaging.setup(self._db, self.auth)
            self._cache['messaging'] = messaging
        return self._cache['messaging']
    
    @property
    def servers(self):
        """Get the servers module (lazy loaded)."""
        if 'servers' not in self._cache:
            from src.core import servers
            self._reset_module(servers)
            servers.setup(self._db, self.auth, self.messaging)
            self._cache['servers'] = servers
        return self._cache['servers']
    
    @property
    def relationships(self):
        """Get the relationships module (lazy loaded)."""
        if 'relationships' not in self._cache:
            from src.core import relationships
            self._reset_module(relationships)
            relationships.setup(self._db, self.auth, self.servers)
            self._cache['relationships'] = relationships
        return self._cache['relationships']
    
    @property
    def presence(self):
        """Get the presence module (lazy loaded)."""
        if 'presence' not in self._cache:
            from src.core import presence
            self._reset_module(presence)
            presence.setup(self._db, self.auth, self.relationships, self.servers)
            self._cache['presence'] = presence
        return self._cache['presence']
    
    @property
    def reactions(self):
        """Get the reactions module (lazy loaded)."""
        if 'reactions' not in self._cache:
            from src.core import reactions
            self._reset_module(reactions)
            reactions.setup(self._db, self.messaging, self.servers, self.relationships)
            self._cache['reactions'] = reactions
        return self._cache['reactions']
    
    @property
    def embeds(self):
        """Get the embeds module (lazy loaded)."""
        if 'embeds' not in self._cache:
            from src.core import embeds
            self._reset_module(embeds)
            embeds.setup(self._db, self.messaging, self.servers)
            self._cache['embeds'] = embeds
        return self._cache['embeds']
    
    @property
    def webhooks(self):
        """Get the webhooks module (lazy loaded)."""
        if 'webhooks' not in self._cache:
            from src.core import webhooks
            self._reset_module(webhooks)
            webhooks.setup(self._db, self.auth, self.messaging, self.servers, self.embeds)
            self._cache['webhooks'] = webhooks
        return self._cache['webhooks']
    
    @property
    def threads(self):
        """Get the threads module (lazy loaded)."""
        if 'threads' not in self._cache:
            from src.core import threads
            self._reset_module(threads)
            threads.setup(self._db, self.auth, self.messaging, self.servers)
            self._cache['threads'] = threads
        return self._cache['threads']
    
    @property
    def notifications(self):
        """Get the notifications module (lazy loaded)."""
        if 'notifications' not in self._cache:
            from src.core import notifications
            self._reset_module(notifications)
            notifications.setup(self._db, self.auth, self.messaging, self.servers)
            self._cache['notifications'] = notifications
        return self._cache['notifications']
    
    @property
    def ratelimit(self):
        """Get the ratelimit module (lazy loaded)."""
        if 'ratelimit' not in self._cache:
            from src.core import ratelimit
            from src.core.ratelimit.storage import MemoryStorage
            self._reset_module(ratelimit)
            storage = MemoryStorage(cleanup_interval=1.0, max_buckets=1000)
            ratelimit.setup(
                storage_backend=storage,
                bot_multiplier=1.5,
                enable_global_limit=True,
            )
            self._cache['ratelimit'] = ratelimit
        return self._cache['ratelimit']
    
    @property
    def voice(self):
        """Get the voice module (lazy loaded)."""
        if 'voice' not in self._cache:
            from src.core import voice
            self._reset_module(voice)
            voice.setup(self._db, self.auth, self.servers)
            self._cache['voice'] = voice
        return self._cache['voice']
    
    @property
    def events(self):
        """Get the events module (lazy loaded)."""
        if 'events' not in self._cache:
            from src.core import events
            self._reset_module(events)
            events.setup(
                relationships_module=self.relationships,
                servers_module=self.servers,
                messaging_module=self.messaging,
            )
            self._cache['events'] = events
        return self._cache['events']
    
    @property
    def media(self):
        """Get the media module (lazy loaded)."""
        if 'media' not in self._cache:
            from src.core import media
            self._reset_module(media)
            media.setup(self._db, self.messaging)
            self._cache['media'] = media
        return self._cache['media']
    
    @property
    def search(self):
        """Get the search module (lazy loaded)."""
        if 'search' not in self._cache:
            from src.core import search
            self._reset_module(search)
            search.setup(self._db, self.auth, self.messaging, self.servers)
            self._cache['search'] = search
        return self._cache['search']

    @property
    def applications(self):
        """Get the applications module (lazy loaded)."""
        if 'applications' not in self._cache:
            from src.core import applications
            self._reset_module(applications)
            applications.setup(self._db, self.auth, self.servers, self.events)
            self._cache['applications'] = applications
        return self._cache['applications']

    @property
    def stickers(self):
        """Get the stickers module (lazy loaded)."""
        if 'stickers' not in self._cache:
            from src.core import stickers
            self._reset_module(stickers)
            stickers.setup(self._db, self.messaging, self.servers)
            self._cache['stickers'] = stickers
        return self._cache['stickers']

    @property
    def polls(self):
        """Get the polls module (lazy loaded)."""
        if 'polls' not in self._cache:
            from src.core import polls
            self._reset_module(polls)
            polls.setup(self._db, self.messaging)
            self._cache['polls'] = polls
        return self._cache['polls']

    @property
    def soundboard(self):
        """Get the soundboard module (lazy loaded)."""
        if 'soundboard' not in self._cache:
            from src.core import soundboard
            self._reset_module(soundboard)
            soundboard.setup(self._db, self.servers)
            self._cache['soundboard'] = soundboard
        return self._cache['soundboard']

    @property
    def settings(self):
        """Get the settings module (lazy loaded)."""
        if 'settings' not in self._cache:
            from src.core import settings
            self._reset_module(settings)
            settings.setup(self._db)
            self._cache['settings'] = settings
        return self._cache['settings']

    def get_api(self):
        """
        Get the API module with all dependencies setup.
        
        This is a method rather than property because it requires
        all modules to be initialized.
        """
        if 'api' not in self._cache:
            import src.api as api
            
            api.setup(
                db=self._db,
                auth_module=self.auth,
                messaging_module=self.messaging,
                servers_module=self.servers,
                relationships_module=self.relationships,
                presence_module=self.presence,
                reactions_module=self.reactions,
                embeds_module=self.embeds,
                webhooks_module=self.webhooks,
                settings_module=self.settings,
            )
            self._cache['api'] = api
        return self._cache['api']
    
    def reset_all(self):
        """
        Reset all cached modules.
        
        Useful for tests that need completely fresh module state.
        """
        for name, module in self._cache.items():
            if name != 'api':  # API doesn't have _manager
                try:
                    self._reset_module(module)
                except AttributeError:
                    pass
        self._cache.clear()
    
    def is_loaded(self, module_name: str) -> bool:
        """Check if a module has been loaded."""
        return module_name in self._cache
