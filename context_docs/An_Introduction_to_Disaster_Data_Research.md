# An Introduction to Disaster Data Research

This research effort looks to understand how and why our built environment, particularly historic buildings, is affected by disasters like tornadoes and floods. The work you will do is the foundation for creating safer, more resilient communities and preserving our cultural heritage.

This document will introduce you to the core concepts behind our project: why we care so deeply about disaster data, what we can do with it, and how your contribution fits into the larger research landscape.

### Why Do We Care About Disaster Data?

When a disaster strikes, the damage can seem random and chaotic. But it isn't! Certain buildings are damaged while others nearby survive. Why? The answers are hidden in the details, things like the building's age, its construction materials, the shape of its roof, its position on the street, and dozens of other factors.

Our goal is to move from anecdotal observations to data-driven conclusions. By systematically collecting information on individual buildings impacted by a disaster, we can begin to uncover the patterns that determine vulnerability. This research is especially important for historic structures, which are often irreplaceable links to our past and are frequently more vulnerable due to their age and construction methods.

### Your Work: Building the Dataframe

Your primary task will be to help build a **dataframe** for each disaster we study.

What is a dataframe?

Think of it as a highly detailed and structured spreadsheet. Each row in the spreadsheet represents a single building that was in the disaster's impact zone. Each column represents a specific piece of information, or a "feature," about that building—things like its location, foundation type, wall materials, or the number of stories.

Why do we collect data this way?

This structured approach is powerful because it turns real-world, qualitative observations ("an old brick building with a flat roof") into quantitative data that can be analyzed by computers. This methodical data collection, guided by our data input guide, is the single most important step in our research. Without clean, accurate, and consistent data, no meaningful analysis is possible.

### The Goal: Feature Importance and Answering the Big Questions

Once a dataframe is complete, we can use powerful analytical techniques to achieve our ultimate goal: determining **feature importance**.

What is feature importance?

Feature importance is a method used in data science and machine learning to figure out which features (or columns in our dataframe) are the most influential in predicting an outcome. In our case, the outcome is the level of damage a building sustained.

Essentially, we are asking the computer to act like a detective and tell us which "clues" (building features) are most critical for solving the "mystery" of why a building was damaged.

This analysis allows us to ask and answer very specific questions:

- Is the year a building was built more critical to its survival than its wall materials?
- Do buildings located within a dense city block perform better against high winds than isolated buildings?
- Are certain foundation types more vulnerable to flood damage than others?
- Do buildings with recent retrofits show measurably less damage?

The data you collect will fuel two types of publications:

1. **Data Papers:** These papers (like the Mayfield one) formally document a dataset, making it available to researchers worldwide and establishing a baseline for future studies.
2. **Analytical Papers:** These papers (like the Beirut and Ukraine ones) use the data to perform feature importance analysis, uncovering the key attributes that drive vulnerability and providing actionable recommendations for engineers, architects, and policymakers to create more resilient buildings and communities.

Your work is the starting point for all of this! The rows you add to our spreadsheets will become the evidence we use to build a stronger, safer, and better-documented world.
