/*
# Licensed Materials - Property of IBM
# Copyright IBM Corp. 2019
 */
package com.ibm.streamsx.topology.internal.context.streamsrest;

import static com.ibm.streamsx.topology.context.ContextProperties.KEEP_ARTIFACTS;
import static com.ibm.streamsx.topology.generator.spl.SPLGenerator.getSPLCompatibleName;
import static com.ibm.streamsx.topology.internal.context.remote.DeployKeys.createJobConfigOverlayFile;
import static com.ibm.streamsx.topology.internal.gson.GsonUtilities.jboolean;
import static com.ibm.streamsx.topology.internal.gson.GsonUtilities.object;

import java.io.File;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.ibm.streamsx.rest.build.Artifact;
import com.ibm.streamsx.rest.build.Build;
import com.ibm.streamsx.rest.build.BuildService;
import com.ibm.streamsx.rest.internal.RestUtils;
import com.ibm.streamsx.topology.context.ContextProperties;
import com.ibm.streamsx.topology.internal.context.remote.BuildRemoteContext;
import com.ibm.streamsx.topology.internal.context.remote.SubmissionResultsKeys;
import com.ibm.streamsx.topology.internal.gson.GsonUtilities;

/**
 * Streams V5 (ICP4D) build service context.
 */
public class BuildServiceContext extends BuildRemoteContext<BuildService> {

    private boolean downloadArtifacts = true;
    private Build build = null;
    private String buildNameSPLCompatible = null;

    /**
     * @param downloadArtifacts
     */
    public BuildServiceContext (boolean downloadArtifacts) {
        super();
        this.downloadArtifacts = downloadArtifacts;
    }

    /**
     * returns the Build object from the application build (SAB build) or <tt>null</tt> if a build is not yet created.
     * @return the applicationBuild
     */
    protected Build getApplicationBuild() {
        return build;
    }

    /**
     * creates a BuildServiceContext that downloads the build artifacts
     */
    public BuildServiceContext() {
        this (true);
    }

    @Override
    public Type getType() {
        return Type.BUNDLE;
    }
    
    private void setBuildName(String name) {
        this.buildNameSPLCompatible = getSPLCompatibleName(name);
    }
    
    public String getBuildName() {
        return this.buildNameSPLCompatible;
    }

    protected boolean sslVerify(JsonObject deploy) {
        if (deploy.has(ContextProperties.SSL_VERIFY))
            return GsonUtilities.jboolean(deploy, ContextProperties.SSL_VERIFY);
        return true;
    }

    @Override
    protected BuildService createSubmissionContext(JsonObject deploy) throws Exception {
        JsonObject serviceDefinition = object(deploy, StreamsKeys.SERVICE_DEFINITION);
        if (serviceDefinition != null)
            return BuildService.ofServiceDefinition(serviceDefinition, sslVerify(deploy));

        // Remote environment context set through environment variables.
        return BuildService.ofEndpoint(null, null, null, null, sslVerify(deploy));
    }

    @Override
    protected JsonObject submitBuildArchive(BuildService context, File buildArchive,
            JsonObject deploy, JsonObject jco, String buildName,
            JsonObject buildConfig) throws Exception {

        if (!sslVerify(deploy))
            context.allowInsecureHosts();

        setBuildName(buildName);
        buildName = getBuildName() + "_" + RestUtils.randomHex(16);

        report("Building bundle");
        this.build = context.createBuild(buildName, buildConfig);
        try {

            JsonObject result = new JsonObject();
            result.add(SubmissionResultsKeys.SUBMIT_METRICS, build.getMetrics());
            JsonObject buildInfo = new JsonObject();
            buildInfo.addProperty("name", build.getName());
            result.add("build", buildInfo);

            build.uploadArchiveAndBuild(buildArchive);

            if (!"built".equals(build.getStatus())) {
                throw new IllegalStateException("Error submitting archive for build: " + buildName);
            }

            JsonArray artifacts = new JsonArray();
            buildInfo.add("artifacts", artifacts);
            if (!build.getArtifacts().isEmpty()) {
                if (!this.downloadArtifacts) {
                    for (Artifact artifact : build.getArtifacts()) {
                        JsonObject buildArtifact = new JsonObject();
                        buildArtifact.addProperty("sabUrl", artifact.getURL());
                        artifacts.add(buildArtifact);
                    }
                }
                else {
                    report("Downloading bundle");
                    final long startDownloadSabTime = System.currentTimeMillis();
                    for (Artifact artifact : build.getArtifacts()) {
                        File sab = artifact.download(null);
                        JsonObject sabInfo = new JsonObject();
                        sabInfo.addProperty("name", artifact.getName());
                        sabInfo.addProperty("size", artifact.getSize());
                        sabInfo.addProperty("location", sab.getAbsolutePath());
                        sabInfo.addProperty("url", artifact.getURL());
                        artifacts.add(sabInfo);
                    }
                    final long endDownloadSabTime = System.currentTimeMillis();
                    build.getMetrics().addProperty(SubmissionResultsKeys.DOWNLOAD_SABS_TIME,
                            (endDownloadSabTime - startDownloadSabTime));


                    if (artifacts.size() == 1) {
                        String location = GsonUtilities
                                .jstring(artifacts.get(0).getAsJsonObject(), "location");

                        result.addProperty(SubmissionResultsKeys.BUNDLE_PATH, location);

                        // Create a Job Config Overlays file if this is creating
                        // a sab for subsequent distributed deployment
                        // or keepArtifacts is set.
                        final File sabFile = new File(location);
                        final String sabBaseName = sabFile.getName().substring(0, sabFile.getName().length()-4);
                        final int lastDot = sabBaseName.lastIndexOf('.');
                        final String namespace, name;
                        if (lastDot == -1) {                      
                            namespace = null;
                            name = sabBaseName;
                        } else {
                            namespace = sabBaseName.substring(0, lastDot);
                            name = sabBaseName.substring(lastDot+1);
                        }
                        if (getClass() == BuildServiceContext.class || jboolean(deploy, KEEP_ARTIFACTS)) {
                            createJobConfigOverlayFile(sabFile.getParentFile(),
                                    jco, namespace, name, result);
                        }

                    }

                }
            }
            postBuildAction(deploy, jco, result);

            return result;
        } finally {
            if (this.downloadArtifacts) {
                deleteBuilds(build);
            }
        }
    }


    /**
     * Deletes Build from the build service.
     * <tt>topology.keepArtifacts</tt> is <i>not</i> evaluated here.
     * @param builds The builds to be deleted
     */
    protected void deleteBuilds (Build... builds) {
        if (builds != null && builds.length > 0) {
            for (Build b: builds) {
                if (b != null) {
                    try {
                        b.delete();
                    }
                    catch (Exception e) {
                        final String buildId = b.getId() == null? "": b.getId();
                        TRACE.warning("failed to delete build " + buildId + ": " + e.toString());
                    }
                }
            }
        }
    }
    
    protected void postBuildAction(JsonObject deploy, JsonObject jco, JsonObject result) throws Exception {
    }
}
