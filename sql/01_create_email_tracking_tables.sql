/*
    Marketing Campaign Studio — SQL Server tracking tables

    Run this script against the Marketing database before enabling bulk delivery.
    It is idempotent: existing tables are left unchanged.
*/

USE [Marketing];
GO

SET ANSI_NULLS ON;
GO

SET QUOTED_IDENTIFIER ON;
GO

IF OBJECT_ID(N'dbo.EmailCampaigns', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.EmailCampaigns
    (
        CampaignID nvarchar(100) NOT NULL,
        Subject nvarchar(500) NOT NULL,
        Status nvarchar(20) NOT NULL
            CONSTRAINT DF_EmailCampaigns_Status DEFAULT (N'PENDING'),
        CreatedAt datetime2(7) NOT NULL
            CONSTRAINT DF_EmailCampaigns_CreatedAt DEFAULT (SYSDATETIME()),
        ApprovedAt datetime2(7) NULL,
        CONSTRAINT PK_EmailCampaigns PRIMARY KEY CLUSTERED (CampaignID ASC)
    );
END;
GO

IF OBJECT_ID(N'dbo.EmailRecipients', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.EmailRecipients
    (
        ID int IDENTITY(1, 1) NOT NULL,
        CampaignID nvarchar(100) NOT NULL,
        Email nvarchar(320) NOT NULL,
        Status nvarchar(20) NOT NULL
            CONSTRAINT DF_EmailRecipients_Status DEFAULT (N'PENDING'),
        SentAt datetime2(7) NULL,
        ErrorMessage nvarchar(1000) NULL,
        CONSTRAINT PK_EmailRecipients PRIMARY KEY CLUSTERED (ID ASC),
        CONSTRAINT UQ_EmailRecipients_Campaign_Email UNIQUE NONCLUSTERED (CampaignID ASC, Email ASC),
        CONSTRAINT FK_EmailRecipients_Campaign
            FOREIGN KEY (CampaignID) REFERENCES dbo.EmailCampaigns (CampaignID)
    );
END;
GO

/* Optional verification */
SELECT
    TableName = t.name,
    ColumnCount = COUNT(c.column_id)
FROM sys.tables AS t
JOIN sys.columns AS c ON c.object_id = t.object_id
WHERE SCHEMA_NAME(t.schema_id) = N'dbo'
  AND t.name IN (N'EmailCampaigns', N'EmailRecipients')
GROUP BY t.name
ORDER BY t.name;
GO
