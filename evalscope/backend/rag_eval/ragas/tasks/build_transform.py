import asyncio
from langchain_core.documents import Document
from ragas.embeddings import BaseRagasEmbeddings
from ragas.llms import BaseRagasLLM
from ragas.testset.graph import NodeType
from ragas.testset.transforms.engine import Parallel
from ragas.testset.transforms.extractors import EmbeddingExtractor, HeadlinesExtractor, SummaryExtractor
from ragas.testset.transforms.extractors.llm_based import NERExtractor, ThemesExtractor
from ragas.testset.transforms.filters import CustomNodeFilter
from ragas.testset.transforms.relationship_builders import CosineSimilarityBuilder, OverlapScoreBuilder
from ragas.testset.transforms.splitters import HeadlineSplitter
from ragas.utils import num_tokens_from_string
from typing import List

from .translate_prompt import translate_prompts


def default_transforms(
    documents: List[Document],
    llm: BaseRagasLLM,
    embedding_model: BaseRagasEmbeddings,
    language: str,
):
    """
    Creates and returns a default set of transforms for processing a knowledge graph.

    This function defines a series of transformation steps to be applied to a
    knowledge graph, including extracting summaries, keyphrases, titles,
    headlines, and embeddings, as well as building similarity relationships
    between nodes.

    """

    def count_doc_length_bins(documents, bin_ranges):
        data = [num_tokens_from_string(doc.page_content) for doc in documents]
        bins = {f'{start}-{end}': 0 for start, end in bin_ranges}

        for num in data:
            for start, end in bin_ranges:
                if start <= num <= end:
                    bins[f'{start}-{end}'] += 1
                    break  # Move to the next number once it’s placed in a bin

        return bins

    def filter_doc_with_num_tokens(node, min_num_tokens=500):
        return (node.type == NodeType.DOCUMENT
                and num_tokens_from_string(node.properties['page_content']) > min_num_tokens)

    def filter_docs(node):
        return node.type == NodeType.DOCUMENT

    def filter_chunks(node):
        return node.type == NodeType.CHUNK

    bin_ranges = [(0, 100), (101, 500), (501, 100000)]
    result = count_doc_length_bins(documents, bin_ranges)
    result = {k: v / len(documents) for k, v in result.items()}

    if result['501-100000'] >= 0.25:
        headline_extractor = HeadlinesExtractor(llm=llm, filter_nodes=lambda node: filter_doc_with_num_tokens(node))
        splitter = HeadlineSplitter(min_tokens=500)
        summary_extractor = SummaryExtractor(llm=llm, filter_nodes=lambda node: filter_doc_with_num_tokens(node))

        theme_extractor = ThemesExtractor(llm=llm, filter_nodes=lambda node: filter_chunks(node))
        ner_extractor = NERExtractor(llm=llm, filter_nodes=lambda node: filter_chunks(node))

        summary_emb_extractor = EmbeddingExtractor(
            embedding_model=embedding_model,
            property_name='summary_embedding',
            embed_property_name='summary',
            filter_nodes=lambda node: filter_doc_with_num_tokens(node),
        )

        cosine_sim_builder = CosineSimilarityBuilder(
            property_name='summary_embedding',
            new_property_name='summary_similarity',
            threshold=0.7,
            filter_nodes=lambda node: filter_doc_with_num_tokens(node),
        )

        ner_overlap_sim = OverlapScoreBuilder(threshold=0.01, filter_nodes=lambda node: filter_chunks(node))

        node_filter = CustomNodeFilter(llm=llm, filter_nodes=lambda node: filter_chunks(node))

        # translate prompts
        asyncio.run(
            translate_prompts(
                prompts=[headline_extractor, summary_extractor, theme_extractor, ner_extractor, node_filter],
                target_lang=language,
                llm=llm,
                adapt_instruction=True,
            ))

        transforms = [
            headline_extractor,
            splitter,
            summary_extractor,
            node_filter,
            Parallel(summary_emb_extractor, theme_extractor, ner_extractor),
            Parallel(cosine_sim_builder, ner_overlap_sim),
        ]
    elif result['101-500'] >= 0.25:
        summary_extractor = SummaryExtractor(llm=llm, filter_nodes=lambda node: filter_doc_with_num_tokens(node, 100))
        summary_emb_extractor = EmbeddingExtractor(
            embedding_model=embedding_model,
            property_name='summary_embedding',
            embed_property_name='summary',
            filter_nodes=lambda node: filter_doc_with_num_tokens(node, 100),
        )

        ner_extractor = NERExtractor(llm=llm)
        ner_overlap_sim = OverlapScoreBuilder(threshold=0.01)
        theme_extractor = ThemesExtractor(llm=llm, filter_nodes=lambda node: filter_docs(node))
        node_filter = CustomNodeFilter(llm=llm)

        # translate prompts
        asyncio.run(
            translate_prompts(
                prompts=[summary_extractor, theme_extractor, ner_extractor, node_filter],
                target_lang=language,
                llm=llm,
                adapt_instruction=True,
            ))

        transforms = [
            summary_extractor,
            node_filter,
            Parallel(summary_emb_extractor, theme_extractor, ner_extractor),
            ner_overlap_sim,
        ]
    else:
        raise ValueError('Documents appears to be too short (ie 100 tokens or less). Please provide longer documents.')

    return transforms
