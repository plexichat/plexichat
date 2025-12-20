"""Comprehensive Polls tests targeting 80%+ coverage."""
import pytest
from src.core.polls.exceptions import *

class TestPollErrors:
    def test_invalid_question_empty(self, poll_manager):
        """Poll question cannot be empty."""
        with pytest.raises(InvalidPollQuestionError):
            poll_manager._validate_question("")
    
    def test_invalid_question_too_long(self, poll_manager):
        """Poll question too long."""
        with pytest.raises(InvalidPollQuestionError):
            poll_manager._validate_question("x" * 500)
    
    def test_invalid_option_empty(self, poll_manager):
        """Poll option cannot be empty."""
        with pytest.raises(InvalidPollOptionError):
            poll_manager._validate_option("")
    
    def test_too_few_options(self, poll_manager, test_db):
        """Need minimum number of options."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        with pytest.raises(PollOptionLimitError):
            poll_manager.create_poll(1, 1, "Question?", ["Option 1"])
    
    def test_too_many_options(self, poll_manager, test_db, monkeypatch):
        """Cannot exceed max options."""
        monkeypatch.setitem(poll_manager._config, 'max_options', 3)
        
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        with pytest.raises(PollOptionLimitError):
            poll_manager.create_poll(1, 1, "Question?", ["A", "B", "C", "D"])
    
    def test_invalid_duration(self, poll_manager):
        """Invalid poll duration."""
        with pytest.raises(InvalidPollDurationError):
            poll_manager._validate_duration(0)
        
        with pytest.raises(InvalidPollDurationError):
            poll_manager._validate_duration(1000)
    
    def test_vote_ended_poll(self, poll_manager, test_db):
        """Cannot vote on ended poll."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"], duration_hours=1)
        
        poll_manager._db.execute("UPDATE poll_polls SET ends_at = ? WHERE id = ?", (1, poll.id))
        
        with pytest.raises(PollEndedError):
            poll_manager.vote(1, poll.id, [poll.options[0].id])
    
    def test_vote_already_voted(self, poll_manager, test_db):
        """Cannot vote twice."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"])
        poll_manager.vote(1, poll.id, [poll.options[0].id])
        
        with pytest.raises(AlreadyVotedError):
            poll_manager.vote(1, poll.id, [poll.options[1].id])
    
    def test_multiple_choice_not_allowed(self, poll_manager, test_db):
        """Cannot vote multiple when not allowed."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"], allow_multiple_choice=False)
        
        with pytest.raises(MultipleVoteNotAllowedError):
            poll_manager.vote(1, poll.id, [poll.options[0].id, poll.options[1].id])
    
    def test_end_poll_early(self, poll_manager, test_db):
        """Can end poll early."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"], duration_hours=24)
        
        ended = poll_manager.end_poll(1, poll.id)
        assert ended.is_ended
    
    def test_get_poll_results(self, poll_manager, test_db):
        """Get poll results."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"])
        poll_manager.vote(1, poll.id, [poll.options[0].id])
        poll_manager.vote(2, poll.id, [poll.options[1].id])
        
        results = poll_manager.get_results(poll.id)
        assert len(results) >= 2
    
    def test_remove_vote(self, poll_manager, test_db):
        """Can remove vote."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"])
        poll_manager.vote(1, poll.id, [poll.options[0].id])
        
        assert poll_manager.remove_vote(1, poll.id)


class TestPollCreation:
    """Test poll creation."""
    
    def test_create_basic_poll(self, poll_manager, test_db):
        """Create basic poll."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Favorite color?", ["Red", "Blue", "Green"])
        
        assert poll.question == "Favorite color?"
        assert len(poll.options) == 3
    
    def test_create_poll_with_duration(self, poll_manager, test_db):
        """Create poll with duration."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"], duration_hours=24)
        
        assert poll.duration_hours == 24
        assert poll.ends_at is not None
    
    def test_create_multiple_choice_poll(self, poll_manager, test_db):
        """Create multiple choice poll."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Pick all that apply", ["A", "B", "C"], allow_multiple_choice=True)
        
        assert poll.allow_multiple_choice
    
    def test_create_anonymous_poll(self, poll_manager, test_db):
        """Create anonymous poll."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"], anonymous=True)
        
        assert poll.anonymous


class TestPollVoting:
    """Test poll voting."""
    
    def test_vote_single_choice(self, poll_manager, test_db):
        """Vote in single choice poll."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"])
        poll_manager.vote(1, poll.id, [poll.options[0].id])
        
        results = poll_manager.get_results(poll.id)
        assert results[poll.options[0].id] >= 1
    
    def test_vote_multiple_choice(self, poll_manager, test_db):
        """Vote in multiple choice poll."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B", "C"], allow_multiple_choice=True)
        poll_manager.vote(1, poll.id, [poll.options[0].id, poll.options[1].id])
        
        results = poll_manager.get_results(poll.id)
        assert results[poll.options[0].id] >= 1
        assert results[poll.options[1].id] >= 1
    
    def test_change_vote(self, poll_manager, test_db):
        """Change vote."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"])
        poll_manager.vote(1, poll.id, [poll.options[0].id])
        poll_manager.remove_vote(1, poll.id)
        poll_manager.vote(1, poll.id, [poll.options[1].id])
        
        results = poll_manager.get_results(poll.id)
        assert results[poll.options[1].id] >= 1
    
    def test_vote_invalid_option(self, poll_manager, test_db):
        """Cannot vote for invalid option."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"])
        
        with pytest.raises(InvalidPollOptionError):
            poll_manager.vote(1, poll.id, [99999])


class TestPollResults:
    """Test poll result functionality."""
    
    def test_get_detailed_results(self, poll_manager, test_db):
        """Get detailed poll results."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000), (3, 1, 3, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B", "C"])
        poll_manager.vote(1, poll.id, [poll.options[0].id])
        poll_manager.vote(2, poll.id, [poll.options[0].id])
        poll_manager.vote(3, poll.id, [poll.options[1].id])
        
        results = poll_manager.get_detailed_results(poll.id)
        assert results.total_votes >= 3
    
    def test_get_voters_anonymous_poll(self, poll_manager, test_db):
        """Cannot get voters for anonymous poll."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"], anonymous=True)
        poll_manager.vote(1, poll.id, [poll.options[0].id])
        
        with pytest.raises(PollAnonymousError):
            poll_manager.get_option_voters(poll.options[0].id)
    
    def test_get_voters_public_poll(self, poll_manager, test_db):
        """Get voters for public poll."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"], anonymous=False)
        poll_manager.vote(1, poll.id, [poll.options[0].id])
        poll_manager.vote(2, poll.id, [poll.options[0].id])
        
        voters = poll_manager.get_option_voters(poll.options[0].id)
        assert len(voters) >= 2


class TestPollManagement:
    """Test poll management."""
    
    def test_end_poll_not_creator(self, poll_manager, test_db):
        """Cannot end poll not created by you."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"])
        
        with pytest.raises(PermissionDeniedError):
            poll_manager.end_poll(2, poll.id)
    
    def test_delete_poll(self, poll_manager, test_db):
        """Delete poll."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"])
        
        assert poll_manager.delete_poll(1, poll.id)
    
    def test_delete_poll_not_creator(self, poll_manager, test_db):
        """Cannot delete poll not created by you."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"])
        
        with pytest.raises(PermissionDeniedError):
            poll_manager.delete_poll(2, poll.id)
    
    def test_get_poll_not_found(self, poll_manager):
        """Get nonexistent poll."""
        poll = poll_manager.get_poll(99999)
        assert poll is None
    
    def test_get_user_votes(self, poll_manager, test_db):
        """Get user's votes in poll."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"], allow_multiple_choice=True)
        poll_manager.vote(1, poll.id, [poll.options[0].id, poll.options[1].id])
        
        votes = poll_manager.get_user_votes(1, poll.id)
        assert len(votes) >= 2
    
    def test_has_voted(self, poll_manager, test_db):
        """Check if user has voted."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test', 1000, 1000, 'text')")
        
        poll = poll_manager.create_poll(1, 1, "Question?", ["A", "B"])
        poll_manager.vote(1, poll.id, [poll.options[0].id])
        
        assert poll_manager.has_voted(1, poll.id)
