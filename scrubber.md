# Motivation
Accelerate resume and cover letter generation, catered to a scrubbed
job posting from ECC portal.

# Functionality
- scrub ECC website (https://ecc-uoft-coop-csm.symplicity.com/students/index.php?signin_tab=0&signin_tab=0) for job postings I specify
- cater my master template to the keywords in the job listing
    - master template has everything about me. the generated template
      copies bits of master template relevent to job posting such that
      it takes up 1 page max (configurable). It is a LaTeX file.
- draft a cover letter based on a repository of my own cover letters
  and other examples of high quality cover letters (from the internet). 
- master template resume found under folder "master/". Reference materials under "ref".
- a file "pref.md" states what type of jobs I am looking for, and you should search for those jobs specifically. find other job listings that you think would be suitable for me too.

# Product
- an agentic framework/harness.The backend will be codex. I envision a
  simple CLI command that starts a framework chat (not in codex, but
  codex is the backend). simply asks for user prompt, then goes
  through a standard set of events, unil finally it generates a
  resume.
- start a "generation" session via CLI. an agent first scrubs through job
  listings and generates a list of keywords. stores these in a text
  file.
- the agent then decides a set of requirements. these requirements
  should be verified via CLI. verification methods must
  follow from this (see the verifications section later).
  resume should achieve (key words found in job listing)
- agent goes to master resume template and pulls out relevant
  experiences and projects
- agent should then sketch a sample resume structure (ideally close to
  master template) and prompt the user to confirm it looks good.
- agent builds resume in LaTeX
- the framework should be coded in python. the cli should have options
  to change model.
- also make claude code backend support
- suggest several candidate resumes, starting with one very faithful to the master template resume (essential copy paste blocks of it into the tailored one), to ones that are more suitable as per your liking.
- each new cover letter/job should be in its own folder under a folder called "projects"

# Verifications
what makes this harness special and what helps it avoid hallucinations
is its verifications structure.

- in the product section we describe how the agent comes up with a set
  of requirements that the user confirms. decompose the resume as a
  tree. we have the top level objective (build a resume) which
  decomposes into its sections (projects, experience, education),
  which decomposes into individual bullet points. at each level of
  this tree the requirements should be met.

  - each individual resume bullet must contain at least one approved
    keyword from the job description; keyword presence must be verified,
    not just asserted by the drafting agent.
  - verify that the mapped keyword appears in both the bullet and the
    job description. Include per-bullet checks and a document-wide check
    that every bullet satisfies this rule in the formal verification.

  I suggest using lean based formal logic to help with
  verifications. here's some inspiration
  https://github.com/arkanemystic/lean-agent-protocol

More inspiration:
https://github.com/career-ops-hq/career-ops

Here are my general points and tips when it comes to resume generation. The **bolded** points are a must.
# Cover letters and resume
- write something customized to role and company. you gotta
  larp. "this company started as yada yada and is now yada yada" is
  apparently eye catching ;-;
- highlight transferable skills (aka LARP)
- created tailored resume --> course codes don't mean anything. state
  the course name (I think??). course
  projects more useful than listing courses, and you can expand on
  that in cover letter
- cover letter suggestion: project --> transferrable skill. especially
  good if you don't have previous experience. include ECs.
- NOTE club projects don't stand out in cover letters because everyone
  applying would say the same thing (esp. if applying through PEY coop)
- 1 page max for finance. upto 2 pages max for most jobs.
- **bullet points should not be longer than two lines. steal word for
  word from job statement**
- **each bullet point should use at least one key word from the job description**
- 12 point font size preferred, min 11 ft size.

- Paragraph 1
    - position + company you are applying for. program, year,
      university, specify PEYcoop.
    - interest sentence: explain why you are interested in working for
      them
    - thesis statement: last sentence. highlight top 2-3 skills that
      the role needs. can be technical or interpersonal

# Interview
- dress to impress
- don't overprepare for an interview. do not sound like a bot.
- you can ask/should ask questions during the interview. memorable
  interviews become conversations.
- do mock interviews. you should have answers to the basic questions.
- behaviour questions --> they want to see how you think. talk through
  your thinking process and how you get to your conclusion.
- MAKE SURE you have questions prepared to ask at the end (example
  shared - "I saw this on your linkdedin what were challenges, what's
  company dynamics, blah blah blah"). you could also ask if there are
  any specific skills you could prepare before the start date.
- every point on your resume should match at least one bullet point in
  the job description
- Each bullet point should be action, description, result. Use
  quantity to further your point.
- try to use action words

# Networking
- this is cooked
- have an elevator pitch about yourself. then grill them. (what was
  their career path, what do they work on, what do they like, lessons
  they've learned)
- better to go to smaller events b/c you will be forgotten
- dm Rylan Coleman for j*b ;-;


# Misc for me
- convert thingy to md and use for cover letter generation.
