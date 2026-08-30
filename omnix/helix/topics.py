"""The bioinformatics subfield map: what the corpus covers, and how it is built.

This module is the single source of truth for two things that must not drift
apart:

* **What gets ingested.** `ingest.py` walks `TOPICS` and runs each `query`
  against PubMed, so the corpus is a deliberate, reproducible sample of the
  field rather than whatever a single search happened to return.
* **What can be answered without a model.** Each topic carries its own
  `summary`, `methods` and `tools`. A question that is really "what is X" or
  "which tools do X" is answered from this table in microseconds, and only
  genuinely open questions cost a model call. That is most of what makes the
  feature feel instant.

The taxonomy is fifteen topics because that is what it took to cover the field
without a bucket so broad it retrieves noise. `aliases` exist because users type
"scRNA-seq", not "single-cell transcriptomics".

WHAT THIS TABLE IS AND IS NOT
-----------------------------
The prose here is background knowledge — the settled, textbook-level shape of
each subfield, the part that does not change between one PubMed sync and the
next. It is deliberately NOT where claims about findings live. Anything specific
enough to need a citation comes from the corpus and is answered with a PMID
attached. When the two disagree, the corpus wins: it is dated and checkable.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Topic:
    key: str
    label: str
    query: str
    summary: str
    methods: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    # Roughly how many papers to pull for this topic. Bigger for subfields that
    # are genuinely bigger, so the corpus is not flat where the field is not.
    depth: int = 60

    @property
    def terms(self) -> tuple[str, ...]:
        """Everything a user might type to mean this topic."""
        return (self.key, self.label.lower()) + tuple(a.lower() for a in self.aliases)


TOPICS: tuple[Topic, ...] = (
    Topic(
        key="alignment",
        label="Sequence alignment",
        query=("sequence alignment algorithm[Title/Abstract] OR "
               "read mapping[Title/Abstract] OR "
               "multiple sequence alignment[Title/Abstract]"),
        summary=(
            "Finding correspondence between biological sequences — the "
            "operation almost every other analysis is built on. Pairwise "
            "alignment is solved exactly by dynamic programming; the whole "
            "field since has been about making that tractable at genome scale, "
            "first with seed-and-extend heuristics and then with compressed "
            "full-text indexes."),
        methods=(
            "Needleman-Wunsch (global, dynamic programming)",
            "Smith-Waterman (local, dynamic programming)",
            "Seed-and-extend heuristics",
            "Burrows-Wheeler transform / FM-index",
            "Progressive and consistency-based multiple alignment",
            "Minimizers and sketching for long reads",
        ),
        tools=("BLAST", "BWA", "Bowtie2", "minimap2", "MAFFT", "MUSCLE",
               "Clustal Omega", "DIAMOND", "HISAT2", "STAR"),
        aliases=("alignment", "read mapping", "blast", "bwa", "minimap2",
                 "msa", "multiple sequence alignment"),
        depth=70,
    ),
    Topic(
        key="assembly",
        label="Genome assembly",
        query=("genome assembly[Title/Abstract] AND "
               "(algorithm[Title/Abstract] OR assembler[Title/Abstract] OR "
               "long-read[Title/Abstract])"),
        summary=(
            "Reconstructing whole genomes from sequencing reads, which are far "
            "shorter than the molecule they came from. Short-read assembly is "
            "dominated by de Bruijn graphs; long reads brought back "
            "overlap-layout-consensus and made telomere-to-telomere assemblies "
            "possible, at the cost of higher per-base error that the consensus "
            "step has to absorb."),
        methods=(
            "De Bruijn graph assembly",
            "Overlap-layout-consensus (OLC)",
            "String graph construction",
            "Scaffolding with Hi-C or optical maps",
            "Polishing and consensus calling",
            "Haplotype-resolved / phased assembly",
        ),
        tools=("SPAdes", "Flye", "hifiasm", "Canu", "Shasta", "MEGAHIT",
               "Velvet", "QUAST", "BUSCO", "Merqury"),
        aliases=("assembly", "assembler", "de novo assembly", "t2t",
                 "telomere-to-telomere", "hifiasm", "flye"),
        depth=60,
    ),
    Topic(
        key="variants",
        label="Variant calling and interpretation",
        query=("variant calling[Title/Abstract] OR "
               "somatic mutation detection[Title/Abstract] OR "
               "structural variant detection[Title/Abstract]"),
        summary=(
            "Deciding where a sample's genome differs from a reference, and "
            "what those differences mean. The statistical problem is separating "
            "true variation from sequencing and mapping error; the harder "
            "downstream problem is interpretation, where most variants found "
            "are of uncertain significance."),
        methods=(
            "Bayesian genotype likelihood models",
            "Local de novo assembly of active regions",
            "Deep-learning pileup classification",
            "Joint calling across cohorts",
            "Split-read and read-depth structural variant detection",
            "ACMG/AMP variant classification",
        ),
        tools=("GATK", "DeepVariant", "FreeBayes", "bcftools", "Strelka2",
               "Manta", "Delly", "VEP", "ANNOVAR", "SnpEff"),
        aliases=("variant calling", "snp", "indel", "structural variant", "sv",
                 "gatk", "deepvariant", "vcf"),
        depth=60,
    ),
    Topic(
        key="rnaseq",
        label="RNA-seq and transcriptomics",
        query=("RNA-seq[Title/Abstract] AND "
               "(differential expression[Title/Abstract] OR "
               "quantification[Title/Abstract] OR pipeline[Title/Abstract])"),
        summary=(
            "Measuring which genes are transcribed, and how much. The "
            "quantification step moved from alignment-based counting to "
            "lightweight pseudo-alignment; the differential-expression step is "
            "a count-based statistical problem where the dominant difficulty is "
            "estimating dispersion from few replicates."),
        methods=(
            "Spliced read alignment",
            "Pseudo-alignment / selective alignment",
            "Negative binomial modelling of counts",
            "Empirical Bayes dispersion shrinkage",
            "Transcript assembly and isoform quantification",
            "Gene set and pathway enrichment",
        ),
        tools=("DESeq2", "edgeR", "limma-voom", "Salmon", "kallisto",
               "StringTie", "featureCounts", "HTSeq", "GSEA", "fgsea"),
        aliases=("rna-seq", "rnaseq", "transcriptomics", "differential "
                 "expression", "deseq2", "salmon", "kallisto"),
        depth=65,
    ),
    Topic(
        key="singlecell",
        label="Single-cell genomics",
        query=("single-cell RNA sequencing[Title/Abstract] AND "
               "(method[Title/Abstract] OR analysis[Title/Abstract] OR "
               "integration[Title/Abstract])"),
        summary=(
            "Resolving measurements to individual cells rather than a bulk "
            "average. The data are sparse, high-dimensional and heavily "
            "affected by technical batch, so most of the method literature is "
            "about normalisation, dimensionality reduction, clustering into "
            "cell types, and integrating datasets that were never meant to be "
            "compared."),
        methods=(
            "Count normalisation and variance stabilisation",
            "PCA followed by neighbour-graph clustering (Leiden/Louvain)",
            "UMAP / t-SNE embedding",
            "Batch correction and dataset integration",
            "Trajectory and pseudotime inference",
            "RNA velocity",
            "Label transfer and reference mapping",
        ),
        tools=("Seurat", "Scanpy", "scVI", "Harmony", "Monocle", "scVelo",
               "CellRanger", "Bioconductor SingleCellExperiment", "scran"),
        aliases=("single cell", "single-cell", "scrna-seq", "scrnaseq",
                 "seurat", "scanpy", "cell type", "umap", "pseudotime"),
        depth=75,
    ),
    Topic(
        key="structure",
        label="Protein structure prediction",
        query=("protein structure prediction[Title/Abstract] AND "
               "(deep learning[Title/Abstract] OR AlphaFold[Title/Abstract] OR "
               "folding[Title/Abstract])"),
        summary=(
            "Predicting three-dimensional structure from sequence. Deep "
            "learning on coevolution signal turned this from an open problem "
            "into a largely solved one for single well-conserved domains, and "
            "moved the frontier to complexes, disordered regions, "
            "conformational ensembles and designed proteins."),
        methods=(
            "Multiple sequence alignment / coevolution features",
            "End-to-end differentiable structure modules",
            "Attention over residue pairs",
            "Template-based modelling and threading",
            "Molecular dynamics refinement",
            "Confidence estimation (pLDDT, PAE)",
        ),
        tools=("AlphaFold", "AlphaFold-Multimer", "ColabFold", "RoseTTAFold",
               "ESMFold", "PyMOL", "Rosetta", "OpenFold", "PDB"),
        aliases=("protein structure", "alphafold", "folding", "structural "
                 "bioinformatics", "esmfold", "rosetta", "pdb"),
        depth=70,
    ),
    Topic(
        key="proteinml",
        label="Protein language models and design",
        query=("protein language model[Title/Abstract] OR "
               "protein design[Title/Abstract] AND deep learning[Title/Abstract]"),
        summary=(
            "Treating protein sequence as a language and learning from "
            "unlabelled sequence at scale. The resulting embeddings transfer to "
            "structure, function and stability prediction without task-specific "
            "alignment, and the generative direction produces sequences that "
            "fold to a specified target."),
        methods=(
            "Masked language modelling on sequence",
            "Transfer learning from embeddings",
            "Inverse folding (structure to sequence)",
            "Diffusion models for backbone generation",
            "Directed evolution guided by model scores",
            "Zero-shot variant effect prediction",
        ),
        tools=("ESM-2", "ESM-3", "ProtBERT", "ProteinMPNN", "RFdiffusion",
               "ProGen", "EVmutation"),
        aliases=("protein language model", "plm", "esm", "protein design",
                 "proteinmpnn", "rfdiffusion", "inverse folding"),
        depth=55,
    ),
    Topic(
        key="phylogenetics",
        label="Phylogenetics and molecular evolution",
        query=("phylogenetic inference[Title/Abstract] OR "
               "phylogenomics[Title/Abstract] OR "
               "molecular evolution[Title/Abstract] AND method[Title/Abstract]"),
        summary=(
            "Inferring evolutionary relationships from sequence. The dominant "
            "frameworks are maximum likelihood and Bayesian inference under "
            "explicit substitution models; the practical difficulties are "
            "model selection, tree-space search, and reconciling gene trees "
            "that disagree with the species tree."),
        methods=(
            "Maximum likelihood tree search",
            "Bayesian MCMC inference",
            "Substitution model selection",
            "Bootstrap and approximate branch support",
            "Coalescent-based species tree estimation",
            "Molecular clock and divergence dating",
        ),
        tools=("IQ-TREE", "RAxML", "MrBayes", "BEAST", "ASTRAL", "MEGA",
               "FastTree", "PhyML"),
        aliases=("phylogenetics", "phylogeny", "tree inference", "iq-tree",
                 "raxml", "beast", "molecular clock"),
        depth=50,
    ),
    Topic(
        key="metagenomics",
        label="Metagenomics and the microbiome",
        query=("metagenomics[Title/Abstract] AND "
               "(taxonomic classification[Title/Abstract] OR "
               "assembly[Title/Abstract] OR microbiome analysis[Title/Abstract])"),
        summary=(
            "Sequencing communities rather than isolates. The core "
            "computational problems are assigning reads to taxa without a "
            "reference genome for most members, assembling genomes out of a "
            "mixture, and doing statistics on compositional data where only "
            "relative abundances are observed."),
        methods=(
            "k-mer based taxonomic classification",
            "Metagenome-assembled genome (MAG) binning",
            "Amplicon sequence variant inference",
            "Compositional data analysis",
            "Functional profiling of gene families",
            "Strain-level resolution",
        ),
        tools=("Kraken2", "Bracken", "MetaPhlAn", "HUMAnN", "QIIME 2", "DADA2",
               "MetaBAT", "CheckM", "mothur"),
        aliases=("metagenomics", "microbiome", "16s", "kraken", "qiime",
                 "mag", "amplicon"),
        depth=60,
    ),
    Topic(
        key="dlgenomics",
        label="Deep learning for genomics",
        query=("deep learning[Title/Abstract] AND "
               "(regulatory genomics[Title/Abstract] OR "
               "gene expression prediction[Title/Abstract] OR "
               "genomic sequence[Title/Abstract])"),
        summary=(
            "Learning the regulatory grammar of DNA directly from sequence — "
            "predicting chromatin accessibility, transcription-factor binding "
            "and expression from the underlying bases. The interesting output "
            "is often not the prediction but the attribution: which bases the "
            "model used, read as a hypothesis about regulatory motifs."),
        methods=(
            "Convolutional sequence models",
            "Dilated convolutions and transformers for long range",
            "In-silico mutagenesis",
            "Attribution methods (DeepLIFT, integrated gradients)",
            "Foundation models pretrained on genomes",
            "Chromatin contact prediction",
        ),
        tools=("Enformer", "Basenji", "DeepSEA", "Basset", "scBasset",
               "Nucleotide Transformer", "DNABERT", "Kipoi"),
        aliases=("deep learning genomics", "regulatory genomics", "enformer",
                 "deepsea", "dnabert", "sequence model"),
        depth=55,
    ),
    Topic(
        key="gwas",
        label="Statistical genetics and GWAS",
        query=("genome-wide association study[Title/Abstract] AND "
               "(method[Title/Abstract] OR polygenic[Title/Abstract] OR "
               "heritability[Title/Abstract])"),
        summary=(
            "Relating genetic variation to traits across populations. The "
            "statistical machinery handles relatedness and population "
            "structure, multiple testing across millions of variants, and the "
            "gap between an associated locus and the gene actually responsible. "
            "Portability of polygenic scores across ancestries remains the "
            "field's most consequential open problem."),
        methods=(
            "Linear and logistic mixed models",
            "Principal components for population structure",
            "LD score regression",
            "Fine-mapping and colocalisation",
            "Polygenic score construction",
            "Mendelian randomisation",
        ),
        tools=("PLINK", "REGENIE", "SAIGE", "BOLT-LMM", "LDSC", "GCTA",
               "SUSIE", "PRS-CS", "METAL"),
        aliases=("gwas", "polygenic", "prs", "heritability", "plink",
                 "mendelian randomization", "fine-mapping"),
        depth=60,
    ),
    Topic(
        key="epigenomics",
        label="Epigenomics and chromatin",
        query=("epigenomics[Title/Abstract] OR ATAC-seq[Title/Abstract] OR "
               "ChIP-seq[Title/Abstract] AND analysis[Title/Abstract]"),
        summary=(
            "Measuring the state of the genome rather than its sequence: which "
            "regions are open, which are marked, how the molecule is folded. "
            "The analysis problems are peak calling against a non-uniform "
            "background, normalising assays with very different signal shapes, "
            "and inferring three-dimensional contacts from ligation data."),
        methods=(
            "Peak calling against local background",
            "Differential accessibility and binding",
            "Motif enrichment and footprinting",
            "Bisulfite alignment and methylation calling",
            "Hi-C normalisation, TAD and loop calling",
            "Chromatin state segmentation",
        ),
        tools=("MACS2", "HOMER", "deepTools", "Bismark", "Juicer", "cooler",
               "ChromHMM", "TOBIAS"),
        aliases=("epigenomics", "atac-seq", "chip-seq", "methylation", "hi-c",
                 "chromatin", "macs2", "peak calling"),
        depth=50,
    ),
    Topic(
        key="spatial",
        label="Spatial and multi-omics integration",
        query=("spatial transcriptomics[Title/Abstract] OR "
               "multi-omics integration[Title/Abstract] AND "
               "method[Title/Abstract]"),
        summary=(
            "Keeping tissue coordinates attached to molecular measurements, and "
            "combining modalities measured on the same cells or samples. The "
            "methods problems are deconvolving spots that contain several "
            "cells, finding spatially variable genes, and building joint "
            "representations across modalities with different noise models."),
        methods=(
            "Spot deconvolution to cell types",
            "Spatially variable gene detection",
            "Neighbourhood and niche analysis",
            "Joint latent-variable models across modalities",
            "Cross-modality imputation",
            "Cell-cell communication inference",
        ),
        tools=("Squidpy", "Giotto", "cell2location", "Seurat v5", "MOFA+",
               "totalVI", "CellChat", "SpaGCN"),
        aliases=("spatial transcriptomics", "spatial omics", "multi-omics",
                 "multiomics", "visium", "deconvolution", "cell2location"),
        depth=60,
    ),
    Topic(
        key="drugdiscovery",
        label="Computational drug discovery",
        query=("virtual screening[Title/Abstract] OR molecular docking"
               "[Title/Abstract] OR drug discovery[Title/Abstract] AND "
               "machine learning[Title/Abstract]"),
        summary=(
            "Using computation to narrow the search for molecules that bind a "
            "target and behave as a drug. Physics-based docking and free-energy "
            "methods trade accuracy against throughput; learned scoring "
            "functions and generative chemistry have shifted the balance, with "
            "the persistent difficulty being that retrospective benchmarks "
            "flatter models that do not prospectively work."),
        methods=(
            "Molecular docking and scoring functions",
            "Free-energy perturbation",
            "Pharmacophore and shape-based screening",
            "QSAR and learned property prediction",
            "Generative molecular design",
            "ADMET prediction",
        ),
        tools=("AutoDock Vina", "Schrodinger Glide", "RDKit", "DeepChem",
               "GROMACS", "OpenMM", "SwissADME", "ChEMBL"),
        aliases=("drug discovery", "docking", "virtual screening", "qsar",
                 "cheminformatics", "rdkit", "admet"),
        depth=55,
    ),
    Topic(
        key="infrastructure",
        label="Workflows, databases and reproducibility",
        query=("bioinformatics workflow[Title/Abstract] OR "
               "reproducibility[Title/Abstract] AND "
               "(pipeline[Title/Abstract] OR database[Title/Abstract] OR "
               "FAIR[Title/Abstract])"),
        summary=(
            "The plumbing the rest of the field runs on: workflow engines that "
            "make a multi-step analysis re-runnable, containers that pin the "
            "software environment, and the public archives that make data "
            "findable. Unglamorous and load-bearing — most published analyses "
            "are irreproducible for infrastructure reasons rather than "
            "scientific ones."),
        methods=(
            "Declarative workflow specification",
            "Containerisation and environment pinning",
            "Provenance capture",
            "FAIR data principles",
            "Continuous testing of pipelines",
            "Controlled-access data governance",
        ),
        tools=("Nextflow", "Snakemake", "nf-core", "CWL", "Galaxy", "Docker",
               "Singularity/Apptainer", "Conda/Bioconda", "GEO", "SRA",
               "Ensembl", "UniProt"),
        aliases=("workflow", "nextflow", "snakemake", "reproducibility",
                 "pipeline", "fair", "database", "galaxy", "containers"),
        depth=50,
    ),
)

BY_KEY: dict[str, Topic] = {t.key: t for t in TOPICS}


def find(text: str) -> list[Topic]:
    """Topics a question plausibly concerns, best match first.

    Deliberately lexical and deliberately cheap: this runs before anything else
    on every question, and its job is only to bias retrieval and pick which
    background note to offer. Retrieval over the corpus is what actually decides
    the answer, so a miss here costs relevance, not correctness.
    """
    low = f" {text.lower()} "
    scored: list[tuple[int, Topic]] = []
    for t in TOPICS:
        score = 0
        for term in t.terms:
            if f" {term} " in low or f" {term}s " in low:
                score += 10 + len(term)      # whole-word hit, longer is better
            elif term in low:
                score += 4 + len(term) // 2  # substring hit
        for tool in t.tools:
            if tool.lower() in low:
                score += 12
        if score:
            scored.append((score, t))
    scored.sort(key=lambda s: -s[0])
    return [t for _, t in scored]


def all_tools() -> dict[str, list[str]]:
    """Every tool named in the taxonomy, by topic. Used by the UI's tool index."""
    return {t.key: list(t.tools) for t in TOPICS}
