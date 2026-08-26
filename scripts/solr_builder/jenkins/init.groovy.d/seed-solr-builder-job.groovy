// Idempotently create the solr_builder pipeline jobs ("Pipeline script from SCM").
// Runs once at first boot from /usr/share/jenkins/ref/init.groovy.d/.
//
// Two jobs run SIDE BY SIDE on separate data stores:
//   solr-builder       legacy postgres pipeline   (scripts/solr_builder/Jenkinsfile)
//   solr-builder-rust  lake/Rust pipeline         (scripts/solr_builder/Jenkinsfile.rust)
//
// Env knobs (set via compose):
//   SEED_BRANCH  branch both jobs track (bare name or */name spec; default master)
//   SEED_REPO    git URL (default https://github.com/internetarchive/openlibrary.git)

import hudson.model.BooleanParameterDefinition
import hudson.model.ParametersDefinitionProperty
import hudson.model.StringParameterDefinition
import hudson.plugins.git.BranchSpec
import hudson.plugins.git.GitSCM
import jenkins.model.Jenkins
import org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition
import org.jenkinsci.plugins.workflow.job.WorkflowJob

def repoUrl = System.getenv("SEED_REPO") ?: "https://github.com/internetarchive/openlibrary.git"
def requested = System.getenv("SEED_BRANCH")
def branchSpec = (requested != null && !requested.isEmpty()) ?
    (requested.startsWith("*/") ? requested : "*/${requested}") : "*/master"

def boolParam = { name, defVal, desc -> new BooleanParameterDefinition(name, defVal, desc) }
def strParam = { name, defVal, desc -> new StringParameterDefinition(name, defVal, desc) }

// Legacy postgres pipeline params (keep in sync with scripts/solr_builder/Jenkinsfile)
def legacyParams() {
    def p = []
    ["WIPE_OLD_POSTGRES", "WIPE_OLD_SOLR"].each { name ->
        p << boolParam(name, false, "If true, removes the current ${name.contains('POSTGRES') ? 'postgres' : 'solr'}")
    }
    ["INDEX_WORKS", "INDEX_ORPHANS", "INDEX_SUBJECTS", "INDEX_AUTHORS", "INDEX_LISTS"].each { name ->
        p << boolParam(name, true, "If true, reindexes ${name.replace('INDEX_', '').toLowerCase()} into solr")
    }
    p << boolParam("SKIP_IA_METADATA", false, "If true, skips fetching edition metadata from archive.org (testing only; ia_* fields will be empty in solr)")
    p << strParam("MAX_CORES", "18", "Max number of simultaneous cores")
    p << strParam("PIP_INDEX_URL", "", "Path to custom PIP index (needed on prod)")
    p << strParam("HTTPS_PROXY", "", "Proxy for HTTP requests (needed on prod)")
    p << strParam("NO_PROXY", "archive.org,openlibrary.org,.archive.org,.openlibrary.org", "No proxy for these domains")
    return p
}

// Rust lake pipeline params (keep in sync with scripts/solr_builder/Jenkinsfile.rust)
def rustParams() {
    def p = []
    p << strParam("DUMP_URL", "https://openlibrary.org/data/ol_dump_latest.txt.gz", "Dump to download (redirects are chased to the dated file)")
    p << strParam("LAKE_HOST_DIR", "/mnt/HC_Volume_106672133/openlibrary", "Host dir holding dumps/lake/solr data; bind-mounted 1:1 into the agent")
    p << boolParam("RESUME", true, "Skip stages whose outputs already exist (chunk files, bronze, etc.)")
    p << boolParam("WIPE_SOLR", false, "Empty the isolated solr_rust_full data dir before loading")
    p << boolParam("FETCH_IA_METADATA", true, "Fetch IA lite metadata so ebook_access/has_fulltext are real (~30-90 min). If false, ocaids stay unclassified")
    p << boolParam("LOAD_SATELLITES", true, "Authors, lists, ratings/reading-log, osp, author aggregates")
    p << boolParam("SMOKE", false, "Tiny slice (3 chunks, sampled satellites) to prove the wiring end-to-end")
    p << strParam("CHUNK_PARALLELISM", "3", "Parallel gold-chunk transforms")
    p << strParam("POST_CONCURRENCY", "8", "Parallel Solr load streams")
    p << boolParam("OPTIMIZE", false, "Run optimize=true&maxSegments=1 at the end (hours; optional)")
    return p
}

def jobs = [
    [name: "solr-builder",      jenkinsfile: "scripts/solr_builder/Jenkinsfile",       params: { legacyParams() }()],
    [name: "solr-builder-rust", jenkinsfile: "scripts/solr_builder/Jenkinsfile.rust",  params: { rustParams() }()],
]

if (System.getenv("ADMIN_PASSWORD") in [null, ""]) {
    println("SEEDER: WARNING - ADMIN_PASSWORD is not set; the seeded 'admin' user has an empty password. Restart with ADMIN_PASSWORD=<password> to fix.")
}

def jenkins = Jenkins.get()
jobs.each { spec ->
    WorkflowJob job = jenkins.getItem(spec.name)
    if (job == null) {
        def scm = new GitSCM(repoUrl)
        scm.branches = [new BranchSpec(branchSpec)]
        def definition = new CpsScmFlowDefinition(scm, spec.jenkinsfile)
        job = jenkins.createProject(WorkflowJob.class, spec.name)
        job.definition = definition
        println("SEEDER: created job '${spec.name}' tracking ${branchSpec} @ ${spec.jenkinsfile}")
    } else {
        println("SEEDER: job '${spec.name}' already exists")
    }
    def existing = job.getProperty(ParametersDefinitionProperty)
    if (existing != null) {
        job.removeProperty(ParametersDefinitionProperty)
    }
    job.addProperty(new ParametersDefinitionProperty(spec.params))
    job.save()
    println("SEEDER: job '${spec.name}' has ${spec.params.size()} parameters; branch spec: " +
        (job.definition instanceof CpsScmFlowDefinition ? job.definition.scm.branches[0].name : "n/a"))
}
