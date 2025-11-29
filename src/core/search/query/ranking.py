"""
Ranking engine - Score and rank search results.
"""

from typing import List, Dict, Any, Optional, Sequence
from dataclasses import dataclass
import math

from ..models import (
    SearchResult,
    MessageSearchResult,
    UserSearchResult,
    ServerSearchResult,
    ParsedQuery,
)


@dataclass
class RankingWeights:
    """Weights for ranking factors."""
    text_relevance: float = 1.0
    recency: float = 0.3
    author_match: float = 0.5
    channel_match: float = 0.3
    exact_phrase: float = 2.0
    pinned: float = 0.2
    attachment: float = 0.1


class RankingEngine:
    """Engine for scoring and ranking search results."""
    
    def __init__(self, weights: Optional[RankingWeights] = None):
        self.weights = weights or RankingWeights()
        self._now_ms = 0
    
    def rank_message_results(
        self,
        results: List[MessageSearchResult],
        parsed_query: ParsedQuery,
        now_ms: Optional[int] = None,
    ) -> List[MessageSearchResult]:
        """
        Rank message search results by relevance.
        
        Args:
            results: List of search results to rank
            parsed_query: Parsed query for context
            now_ms: Current timestamp in milliseconds
            
        Returns:
            Sorted list of results by score (descending)
        """
        if not results:
            return results
        
        self._now_ms = now_ms or 0
        
        for result in results:
            result.score = self._score_message(result, parsed_query)
        
        return sorted(results, key=lambda r: r.score, reverse=True)
    
    def rank_user_results(
        self,
        results: List[UserSearchResult],
        query: str,
        user_id: int,
    ) -> List[UserSearchResult]:
        """
        Rank user search results by relevance.
        
        Args:
            results: List of user results to rank
            query: Search query string
            user_id: ID of user performing search
            
        Returns:
            Sorted list of results by score (descending)
        """
        if not results:
            return results
        
        query_lower = query.lower()
        
        for result in results:
            result.score = self._score_user(result, query_lower, user_id)
        
        return sorted(results, key=lambda r: r.score, reverse=True)
    
    def rank_server_results(
        self,
        results: List[ServerSearchResult],
        query: str,
    ) -> List[ServerSearchResult]:
        """
        Rank server search results by relevance.
        
        Args:
            results: List of server results to rank
            query: Search query string
            
        Returns:
            Sorted list of results by score (descending)
        """
        if not results:
            return results
        
        query_lower = query.lower()
        
        for result in results:
            result.score = self._score_server(result, query_lower)
        
        return sorted(results, key=lambda r: r.score, reverse=True)
    
    def _score_message(
        self,
        result: MessageSearchResult,
        parsed_query: ParsedQuery,
    ) -> float:
        """Calculate score for a message result."""
        score = result.score if result.score > 0 else 1.0
        
        if parsed_query.exact_phrases:
            content_lower = (result.content or "").lower()
            for phrase in parsed_query.exact_phrases:
                if phrase.lower() in content_lower:
                    score += self.weights.exact_phrase
        
        if self._now_ms > 0 and result.created_at > 0:
            age_hours = (self._now_ms - result.created_at) / (1000 * 60 * 60)
            recency_factor = 1.0 / (1.0 + math.log1p(age_hours / 24))
            score += recency_factor * self.weights.recency
        
        if result.is_pinned:
            score += self.weights.pinned
        
        if result.has_attachments:
            score += self.weights.attachment
        
        return score
    
    def _score_user(
        self,
        result: UserSearchResult,
        query: str,
        user_id: int,
    ) -> float:
        """Calculate score for a user result."""
        score = result.score if result.score > 0 else 1.0
        
        username_lower = (result.username or "").lower()
        display_lower = (result.display_name or "").lower()
        
        if username_lower == query:
            score += 5.0
        elif username_lower.startswith(query):
            score += 3.0
        elif query in username_lower:
            score += 1.0
        
        if display_lower == query:
            score += 4.0
        elif display_lower.startswith(query):
            score += 2.0
        elif query in display_lower:
            score += 0.5
        
        if result.mutual_servers > 0:
            score += min(result.mutual_servers * 0.2, 2.0)
        
        if result.mutual_friends > 0:
            score += min(result.mutual_friends * 0.3, 3.0)
        
        return score
    
    def _score_server(
        self,
        result: ServerSearchResult,
        query: str,
    ) -> float:
        """Calculate score for a server result."""
        score = result.score if result.score > 0 else 1.0
        
        name_lower = (result.name or "").lower()
        desc_lower = (result.description or "").lower()
        
        if name_lower == query:
            score += 5.0
        elif name_lower.startswith(query):
            score += 3.0
        elif query in name_lower:
            score += 1.0
        
        if query in desc_lower:
            score += 0.5
        
        for tag in result.tags:
            if query in tag.lower():
                score += 0.3
        
        if result.member_count > 0:
            member_factor = math.log10(result.member_count + 1) * 0.2
            score += min(member_factor, 2.0)
        
        if result.is_verified:
            score += 1.0
        
        return score


def rank_results(
    results: Sequence[SearchResult],
    parsed_query: Optional[ParsedQuery] = None,
    query: Optional[str] = None,
    user_id: Optional[int] = None,
    now_ms: Optional[int] = None,
    weights: Optional[RankingWeights] = None,
) -> List[SearchResult]:
    """
    Rank search results by relevance.
    
    Convenience function using RankingEngine.
    """
    engine = RankingEngine(weights)
    
    if results and isinstance(results[0], MessageSearchResult):
        msg_results = [r for r in results if isinstance(r, MessageSearchResult)]
        ranked = engine.rank_message_results(msg_results, parsed_query or ParsedQuery(raw_query=""), now_ms)
        return list(ranked)
    
    if results and isinstance(results[0], UserSearchResult):
        user_results = [r for r in results if isinstance(r, UserSearchResult)]
        ranked = engine.rank_user_results(user_results, query or "", user_id or 0)
        return list(ranked)
    
    if results and isinstance(results[0], ServerSearchResult):
        server_results = [r for r in results if isinstance(r, ServerSearchResult)]
        ranked = engine.rank_server_results(server_results, query or "")
        return list(ranked)
    
    return list(results)
